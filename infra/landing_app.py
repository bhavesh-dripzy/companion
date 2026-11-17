#!/usr/bin/env python3
"""Deploy only the company website, never the retired Companion app stacks."""
import os

import aws_cdk as cdk

from landing_stack import LandingStack

EXPECTED_ACCOUNT = "616551057455"
account = os.environ.get("CDK_DEFAULT_ACCOUNT", EXPECTED_ACCOUNT)
if account != EXPECTED_ACCOUNT:
    raise RuntimeError(f"Refusing website deployment to account {account}")

app = cdk.App()
LandingStack(
    app,
    "companion-landing",
    domain_name="jatayulabs.com",
    certificate_arn="arn:aws:acm:us-east-1:616551057455:certificate/489a052c-a6a5-437a-9763-2b50a684a394",
    env=cdk.Environment(account=account, region="ap-south-1"),
    termination_protection=True,
)
app.synth()
