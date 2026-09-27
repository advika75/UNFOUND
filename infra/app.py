#!/usr/bin/env python3
import os

import aws_cdk as cdk

from infra.unfound_api_stack import UnfoundApiStack

app = cdk.App()
UnfoundApiStack(
    app,
    "UnfoundApiStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION"),
    ),
)

app.synth()
