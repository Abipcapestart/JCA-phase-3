"""Bedrock-backed Claude client and a structured-output call helper that also
records latency and token usage for every call."""

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from botocore.exceptions import ClientError
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, SystemMessage

from . import config

llm = ChatBedrockConverse(
    model=config.BEDROCK_MODEL_ID,
    region_name=config.AWS_REGION,
)


def _extract_usage(raw_message: Any) -> Dict[str, Optional[int]]:
    """Best-effort token usage extraction across possible AIMessage shapes —
    LangChain's standardized `usage_metadata`, falling back to the raw Bedrock
    Converse `usage` block if that's absent."""
    usage = getattr(raw_message, "usage_metadata", None)
    if usage:
        return {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }

    response_metadata = getattr(raw_message, "response_metadata", None) or {}
    raw_usage = response_metadata.get("usage") or {}
    input_tokens = raw_usage.get("input_tokens", raw_usage.get("inputTokens"))
    output_tokens = raw_usage.get("output_tokens", raw_usage.get("outputTokens"))
    total_tokens = raw_usage.get("total_tokens", raw_usage.get("totalTokens"))
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def call_structured(
    system_prompt: str,
    user_payload: dict,
    schema_model,
    agent_name: str = "unknown",
) -> Tuple[Any, Dict[str, Any]]:
    """Invoke the LLM with a given system prompt, returning (parsed_result, metric).

    parsed_result is an instance of schema_model. metric is a dict with latency_ms,
    input_tokens, output_tokens, total_tokens, model_id, started_at, and agent —
    meant to be appended to PipelineState["metrics"] by the calling node.
    """
    structured_llm = llm.with_structured_output(schema_model, include_raw=True)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=json.dumps(user_payload, indent=2, ensure_ascii=False)),
    ]

    started_at = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()
    try:
        raw_result = structured_llm.invoke(messages)
    except ClientError as e:
        error_message = str(e)
        if "on-demand throughput isn" in error_message or "inference profile" in error_message:
            raise RuntimeError(
                f"[{agent_name}] Bedrock rejected model_id={config.BEDROCK_MODEL_ID!r}: "
                "this model requires an inference profile ID/ARN, not a bare on-demand "
                "model ID. Set BEDROCK_MODEL_ID in .env to the inference profile ID/ARN "
                "shown in the AWS Bedrock console under Cross-region inference profiles "
                "(e.g. 'us.anthropic.claude-sonnet-5') — original error: "
                f"{error_message}"
            ) from e
        raise
    latency_ms = (time.perf_counter() - start) * 1000

    parsed = raw_result.get("parsed")
    raw_message = raw_result.get("raw")
    parsing_error = raw_result.get("parsing_error")

    if parsed is None:
        raise ValueError(
            f"[{agent_name}] LLM output failed to parse into {schema_model.__name__}: "
            f"{parsing_error}"
        )

    usage = _extract_usage(raw_message)
    input_tokens = usage["input_tokens"] or 0
    output_tokens = usage["output_tokens"] or 0
    cost_usd = (
        input_tokens * config.BEDROCK_INPUT_PRICE_PER_1M_TOKENS
        + output_tokens * config.BEDROCK_OUTPUT_PRICE_PER_1M_TOKENS
    ) / 1_000_000

    metric = {
        "agent": agent_name,
        "model_id": config.BEDROCK_MODEL_ID,
        "latency_ms": round(latency_ms, 1),
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "cost_usd": round(cost_usd, 6),
        "started_at": started_at,
    }
    return parsed, metric
