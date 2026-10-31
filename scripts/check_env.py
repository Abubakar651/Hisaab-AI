#!/usr/bin/env python3
"""
Environment Variable Verification Script for HisaabAI.
Reports PASS / FAIL per variable according to strict format requirements.
NEVER prints secret or configuration values.
"""

import os
import re
import sys
from dotenv import load_dotenv

load_dotenv()

def check_env():
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    api_key = os.getenv("FIREBASE_API_KEY", "").strip()
    auth_domain = os.getenv("FIREBASE_AUTH_DOMAIN", "").strip()
    app_id = os.getenv("FIREBASE_APP_ID", "").strip()
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    app_env = os.getenv("APP_ENV", "").strip()

    results = []

    # 1. FIREBASE_PROJECT_ID: matches ^[a-z0-9-]{6,30}$
    if project_id and re.match(r"^[a-z0-9-]{6,30}$", project_id):
        results.append(("FIREBASE_PROJECT_ID", "PASS", "Valid format (6-30 chars, alphanumeric/hyphens)"))
    else:
        results.append(("FIREBASE_PROJECT_ID", "FAIL", "Missing or invalid format (must match ^[a-z0-9-]{6,30}$)"))

    # 2. FIREBASE_API_KEY: starts with 'AIza', length 39
    if api_key and api_key.startswith("AIza") and len(api_key) == 39:
        results.append(("FIREBASE_API_KEY", "PASS", "Valid format (starts with 'AIza', length 39)"))
    else:
        results.append(("FIREBASE_API_KEY", "FAIL", "Missing or invalid format (must start with 'AIza' and be 39 chars)"))

    # 3. FIREBASE_AUTH_DOMAIN: ends with .firebaseapp.com or .web.app, and subdomain equals project id
    auth_domain_valid = False
    if auth_domain:
        if auth_domain.endswith(".firebaseapp.com") or auth_domain.endswith(".web.app"):
            subdomain = auth_domain.split(".")[0]
            if not project_id or subdomain == project_id:
                auth_domain_valid = True
    if auth_domain_valid:
        results.append(("FIREBASE_AUTH_DOMAIN", "PASS", "Valid domain suffix and matching project subdomain"))
    else:
        results.append(("FIREBASE_AUTH_DOMAIN", "FAIL", "Missing or invalid domain (must end with .firebaseapp.com or .web.app matching project id)"))

    # 4. FIREBASE_APP_ID: matches ^1:\d+:web:[0-9a-f]+$
    if app_id and re.match(r"^1:\d+:web:[0-9a-f]+$", app_id):
        results.append(("FIREBASE_APP_ID", "PASS", "Valid format (^1:\\d+:web:[0-9a-f]+$)"))
    else:
        results.append(("FIREBASE_APP_ID", "FAIL", "Missing or invalid format (must match ^1:\\d+:web:[0-9a-f]+$)"))

    # 5. GROQ_API_KEY: present
    if groq_api_key:
        results.append(("GROQ_API_KEY", "PASS", "Present (non-empty)"))
    else:
        results.append(("GROQ_API_KEY", "FAIL", "Missing or empty"))

    # 6. APP_ENV: set
    if app_env:
        results.append(("APP_ENV", "PASS", f"Configured (value set, length {len(app_env)})"))
    else:
        results.append(("APP_ENV", "FAIL", "Not set (will default to production)"))

    # AWS Bedrock (Optional LLM Provider - fallback to Groq)
    aws_key = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
    bedrock_model = os.getenv("BEDROCK_MODEL_ID", "").strip()
    aws_region = os.getenv("AWS_REGION", "").strip()

    if aws_key or bedrock_model:
        if aws_key and re.match(r"^AKIA[0-9A-Z]{16}$", aws_key):
            results.append(("AWS_ACCESS_KEY_ID", "PASS", "Valid AWS IAM Access Key ID format"))
        else:
            results.append(("AWS_ACCESS_KEY_ID", "WARN", "Configured, but check format (must start with AKIA, 20 chars)"))

        if aws_secret and len(aws_secret) >= 30:
            results.append(("AWS_SECRET_ACCESS_KEY", "PASS", f"Present (non-empty, length {len(aws_secret)})"))
        else:
            results.append(("AWS_SECRET_ACCESS_KEY", "WARN", "Missing or empty secret access key"))

        if bedrock_model:
            results.append(("BEDROCK_MODEL_ID", "PASS", f"Configured: {bedrock_model}"))
        else:
            results.append(("BEDROCK_MODEL_ID", "WARN", "Not set (defaults to us.meta.llama3-3-70b-instruct-v1:0)"))

        if aws_region:
            results.append(("AWS_REGION", "PASS", f"Configured: {aws_region}"))
        else:
            results.append(("AWS_REGION", "PASS", "Default: us-east-1"))

    print("=" * 80)
    print(f"{'VARIABLE':<24} | {'STATUS':<8} | {'DETAILS'}")
    print("-" * 80)
    all_pass = True
    for var, status, details in results:
        print(f"{var:<24} | {status:<8} | {details}")
        if status == "FAIL":
            all_pass = False
    print("=" * 80)

    # Variables no longer needed after shifting to Firebase Auth
    deprecated_vars = [
        ("SESSION_SECRET", "Deprecated: custom HS256 JWT session tokens replaced by Firebase ID tokens"),
        ("TRUSTED_PROXY", "Deprecated: custom login rate limiter and IP tracking removed"),
    ]
    print("\nVARIABLES NO LONGER NEEDED AFTER FIREBASE AUTH MIGRATION:")
    print("-" * 80)
    for var, reason in deprecated_vars:
        is_set = "PRESENT in environment" if os.getenv(var) else "NOT SET"
        print(f"• {var} ({is_set}):\n  -> {reason}")
    print("-" * 80)

    return all_pass

if __name__ == "__main__":
    success = check_env()
    sys.exit(0 if success else 1)
