#   Foremast - Pipeline Tooling
#
#   Copyright 2018 Gogo, LLC
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
"""Create Kinesis Stream lambda event"""

import logging

import boto3

from ...utils import get_lambda_alias_arn, get_kinesis_stream_arn

LOG = logging.getLogger(__name__)


def create_kinesis_stream_event(app_name, env, region, rules):
    """Create kinesis stream events from triggers

    Args:
        app_name (str): name of the lambda function
        env (str): Environment/Account for lambda function
        region (str): AWS region of the lambda function
        triggers (list): List of triggers from the settings
    """
    session = boto3.Session(profile_name=env, region_name=region)
    lambda_client = session.client('lambda')

    stream_name = rules.get('stream')
    lambda_alias_arn = get_lambda_alias_arn(app=app_name, account=env, region=region)
    stream_arn = get_kinesis_stream_arn(stream_name=stream_name, account=env, region=region)

    lambda_client.create_event_source_mapping(
            EventSourceArn=stream_arn,
            FunctionName=lambda_alias_arn,
            StartingPosition='TRIM_HORIZON')

    LOG.info("Created Kinesis Stream event for streamc %s", stream_name)
