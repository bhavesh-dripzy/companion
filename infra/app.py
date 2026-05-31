#!/usr/bin/env python3
import os

import aws_cdk as cdk

from companion_stack import CompanionStack
from graph_stack import GraphStack


app = cdk.App()
project_name = app.node.try_get_context("projectName") or "companion"
region = os.getenv("CDK_DEFAULT_REGION") or app.node.try_get_context("defaultRegion")
account = os.getenv("CDK_DEFAULT_ACCOUNT")

graph_stack = GraphStack(
    app,
    f"{project_name}-graph",
    project_name=project_name,
    env=cdk.Environment(account=account, region=region),
)

CompanionStack(
    app,
    f"{project_name}-serverless",
    project_name=project_name,
    profile_cache=graph_stack.profile_cache,
    turns_queue=graph_stack.turns_queue,
    lead_cache=graph_stack.lead_cache,
    env=cdk.Environment(account=account, region=region),
)

app.synth()
