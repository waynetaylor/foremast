#   Foremast - Pipeline Tooling
#
#   Copyright 2016 Gogo, LLC
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
import collections
import json
import logging

import boto3

from ...exceptions import InvalidEventConfiguration
from ...utils import add_lambda_permissions, get_env_credential, get_lambda_arn

LOG = logging.getLogger(__name__)


def validate_rule(app_name='', rule={}):
    """Validate CloudWatch Event rule.

    Args:
        app_name (str): Name of the Lambda Function.
        rule (dict): Trigger rule from settings.

    Returns:
        CloudWatchEventRule: `collections.namedtuple` containing Rule
        properties.

            * description
            * json_input
            * name
            * schedule
    """
    CloudWatchEventRule = collections.namedtuple('CloudWatchEventRule', [
        'description',
        'json_input',
        'name',
        'schedule',
    ])

    json_input = rule.get('json_input', {})
    name = rule.get('rule_name')
    schedule = rule.get('schedule')

    if schedule is None:
        LOG.critical('Schedule is required and no schedule is defined!')
        raise InvalidEventConfiguration('Schedule is required and no schedule is defined!')

    if name is None:
        LOG.critical('Rule name is required and no rule_name is defined!')
        raise InvalidEventConfiguration('Rule name is required and no rule_name is defined!')
    else:
        LOG.info('%s and %s', app_name, name)
        name = "{}_{}".format(app_name, name.replace(' ', '_'))

    description = rule.get('rule_description', '{app} - {name}'.format(app=app_name, name=name))

    event_rule = CloudWatchEventRule(
        description=description,
        json_input=json_input,
        name=name,
        schedule=schedule, )

    LOG.debug('Validated Event Rule: %s', event_rule)
    return event_rule


def create_cloudwatch_event(app_name, env, region, rules):
    """Create cloudwatch event for lambda from rules.

    Args:
        app_name (str): name of the lambda function
        env (str): Environment/Account for lambda function
        region (str): AWS region of the lambda function
        rules (dict): Trigger rules from the settings
    """
    session = boto3.Session(profile_name=env, region_name=region)
    cloudwatch_client = session.client('events')

    event_rule = validate_rule(app_name=app_name, rule=rules)

    lambda_arn = get_lambda_arn(app=app_name, account=env, region=region)

    #Add lambda permissions
    account_id = get_env_credential(env=env)['accountId']
    principal = "events.amazonaws.com"
    statement_id = '{}_cloudwatch_{}'.format(app_name, event_rule.name)
    source_arn = 'arn:aws:events:{}:{}:rule/{}'.format(region, account_id, event_rule.name)
    add_lambda_permissions(
        function=lambda_arn,
        statement_id=statement_id,
        action='lambda:InvokeFunction',
        principal=principal,
        source_arn=source_arn,
        env=env,
        region=region, )

    # Create Cloudwatch rule
    cloudwatch_client.put_rule(
        Name=event_rule.name,
        ScheduleExpression=event_rule.schedule,
        State='ENABLED',
        Description=event_rule.description)

    targets = []
    # TODO: read this one from file event-config-*.json
    json_payload = '{}'.format(json.dumps(event_rule.json_input))

    target = {
        "Id": app_name,
        "Arn": lambda_arn,
        "Input": json_payload,
    }

    targets.append(target)

    put_targets_response = cloudwatch_client.put_targets(Rule=event_rule.name, Targets=targets)
    LOG.debug('Cloudwatch put targets response: %s', put_targets_response)

    LOG.info('Created Cloudwatch event "%s" with schedule: %s', event_rule.name, event_rule.schedule)
