"""Environment configuration for the pipeline.

Reads AWS credentials, region, and the Bedrock model id from a .env file
(via python-dotenv) so the LLM client never needs credentials hardcoded.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# --- AWS Bedrock ---
AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5")
# Note: Claude Sonnet 5 (and most current Claude models) on Bedrock cannot be
# invoked with a bare on-demand model ID — it requires an inference profile
# ID/ARN (the "us."/"eu."/"apac." prefixed form). "us.anthropic.claude-sonnet-5"
# is the value this project's own Phase 2 module used successfully (see its
# Run Summary sheet) — if Bedrock still rejects it, check AWS Bedrock console →
# Cross-region inference profiles for the exact ID/ARN and set BEDROCK_MODEL_ID
# to that in .env.
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are read directly from the environment
# by boto3 — no need to reference them explicitly here.

# --- Token pricing (USD per 1,000,000 tokens), for cost reporting only ---
# Bedrock bills Claude models separately from Anthropic's first-party API and
# AWS can set/change this independently of Anthropic's list price. The defaults
# below are Anthropic's first-party Claude Sonnet 5 rate as a stand-in — verify
# the actual on-demand rate for BEDROCK_MODEL_ID in your AWS account/region via
# the AWS Bedrock console pricing page, and set BEDROCK_INPUT_PRICE_PER_1M_TOKENS
# / BEDROCK_OUTPUT_PRICE_PER_1M_TOKENS in .env if it differs — every cost figure
# this pipeline reports is only as accurate as these two numbers.
BEDROCK_INPUT_PRICE_PER_1M_TOKENS = float(os.getenv("BEDROCK_INPUT_PRICE_PER_1M_TOKENS", "2.00"))
BEDROCK_OUTPUT_PRICE_PER_1M_TOKENS = float(os.getenv("BEDROCK_OUTPUT_PRICE_PER_1M_TOKENS", "10.00"))
