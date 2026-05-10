#!/usr/bin/env python3
import os

import aws_cdk as cdk

from companion_stack import CompanionStack


app = cdk.App()
project_name = app.node.try_get_context("projectName") or "companion"
region = os.getenv("CDK_DEFAULT_REGION") or app.node.try_get_context("defaultRegion")
account = os.getenv("CDK_DEFAULT_ACCOUNT")

CompanionStack(
    app,
    f"{project_name}-serverless",
    project_name=project_name,
    env=cdk.Environment(account=account, region=region),
)

app.synth()
