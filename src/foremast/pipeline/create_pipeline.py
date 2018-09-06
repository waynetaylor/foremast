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
"""Create Pipelines for Spinnaker."""
import collections
import json
import logging
import os
from pprint import pformat

import requests

from ..consts import API_URL, DEFAULT_RUN_AS_USER, EC2_PIPELINE_TYPES, GATE_CA_BUNDLE, GATE_CLIENT_CERT
from ..exceptions import SpinnakerPipelineCreationFailed
from ..utils import ami_lookup, generate_packer_filename, get_details, get_properties, get_subnets, get_template
from .clean_pipelines import clean_pipelines
from .construct_pipeline_block import construct_pipeline_block
from .renumerate_stages import renumerate_stages

PipelineVars = collections.namedtuple('PipelineVars', [
    'env',
    'is_ec2_pipeline',
    'next_env',
    'previous_env',
    'region',
    'region_subnets',
])
"""Pipeline variables for use during iteration.

Attributes:
    env (str): Name of Deployment Environment.
    is_ec2_pipeline (bool): Inform caller to use EC2 logic when :obj:`True`.
    next_env (str): Name of next Deployment Environment.
    previous_env (str): Name of last Deployment Environment.
    region (str): Name of Region.
    region_subnets (list): Available Subnets in the specified Region.

"""


class SpinnakerPipeline:
    """Manipulate Spinnaker Pipelines.

    Args:
        app (str): Application name.
        trigger_job (str): Jenkins trigger job.
        base (str): Base image name (i.e: fedora).
        prop_path (str): Path to the raw.properties.json.
        runway_dir (str): Path to local runway directory.
    """

    def __init__(self, app='', trigger_job='', prop_path='', base='', runway_dir=''):
        self.log = logging.getLogger(__name__)

        self.header = {'content-type': 'application/json'}
        self.here = os.path.dirname(os.path.realpath(__file__))

        self.runway_dir = os.path.expandvars(os.path.expanduser(runway_dir or ''))

        self.base = base
        self.trigger_job = trigger_job
        self.generated = get_details(app=app)
        self.app_name = self.generated.app_name()
        self.group_name = self.generated.project

        self.settings = get_properties(prop_path)
        self.environments = self.settings['pipeline']['env']

    @staticmethod
    def to_dict(pipeline):
        """Make sure we are using a :obj:`dict`."""
        pipeline_dict = None
        if isinstance(pipeline, str):
            pipeline_dict = json.loads(pipeline)
        elif isinstance(pipeline, dict):
            pipeline_dict = pipeline
        else:
            raise ValueError('Pipeline must be dict or str, not {0}: {1}'.format(type(pipeline), pipeline))
        return pipeline_dict

    def before_post_pipeline(self, pipeline_dict):  # pylint: disable=no-self-use
        """Pipeline hook for before sending to Spinnaker."""
        renumerate_stages(pipeline_dict)

        return pipeline_dict

    def after_post_pipeline(self, pipeline_dict):  # pylint: disable=no-self-use
        """Pipeline hook for after sending to Spinnaker."""
        return pipeline_dict

    def post_pipeline(self, pipeline):
        """Send Pipeline JSON to Spinnaker.

        Args:
            pipeline (dict, str): New Pipeline to create.

        """
        pipeline_dict = self.to_dict(pipeline)

        pipeline_dict = self.before_post_pipeline(pipeline_dict)
        self._post_pipeline(pipeline_dict)
        pipeline_dict = self.after_post_pipeline(pipeline_dict)

        return pipeline_dict

    def _post_pipeline(self, pipeline_dict):
        """POST Pipeline to Spinnaker."""
        url = "{0}/pipelines".format(API_URL)

        self.log.debug('Pipeline JSON:\n%s', pipeline_dict)

        response = requests.post(
            url, data=pipeline_dict, headers=self.header, verify=GATE_CA_BUNDLE, cert=GATE_CLIENT_CERT)

        self.log.debug('Pipeline creation response:\n%s', response.text)

        if not response.ok:
            raise SpinnakerPipelineCreationFailed('Pipeline for {0}: {1}'.format(self.app_name, response.json()))

        self.log.info('Successfully created "%s" pipeline in application "%s".', pipeline_dict['name'],
                      pipeline_dict['application'])

        return response

    def render_wrapper(self, region='us-east-1'):
        """Generate the base Pipeline wrapper.

        This renders the non-repeatable stages in a pipeline, like jenkins, baking, tagging and notifications.

        Args:
            region (str): AWS Region.

        Returns:
            dict: Rendered Pipeline wrapper.

        """
        base = self.settings['pipeline']['base']

        if self.base:
            base = self.base

        email = self.settings['pipeline']['notifications']['email']
        slack = self.settings['pipeline']['notifications']['slack']
        baking_process = self.settings['pipeline']['image']['builder']
        provider = 'aws'
        root_volume_size = self.settings['pipeline']['image']['root_volume_size']
        bake_instance_type = self.settings['pipeline']['image']['bake_instance_type']

        ami_id = ami_lookup(name=base, region=region)

        ami_template_file = generate_packer_filename(provider, region, baking_process)

        pipeline_id = self.compare_with_existing(region=region)

        data = {
            'app': {
                'ami_id': ami_id,
                'appname': self.app_name,
                'base': base,
                'environment': 'packaging',
                'region': region,
                'triggerjob': self.trigger_job,
                'run_as_user': DEFAULT_RUN_AS_USER,
                'email': email,
                'slack': slack,
                'root_volume_size': root_volume_size,
                'bake_instance_type': bake_instance_type,
                'ami_template_file': ami_template_file,
                'pipeline': self.settings['pipeline']
            },
            'id': pipeline_id
        }

        self.log.debug('Wrapper app data:\n%s', pformat(data))

        wrapper = get_template(template_file='pipeline/pipeline_wrapper.json.j2', data=data, formats=self.generated)

        return json.loads(wrapper)

    def get_existing_pipelines(self):
        """Get existing pipeline configs for specific application.

        Returns:
            str: Pipeline config json

        """
        url = "{0}/applications/{1}/pipelineConfigs".format(API_URL, self.app_name)
        resp = requests.get(url, verify=GATE_CA_BUNDLE, cert=GATE_CLIENT_CERT)
        assert resp.ok, 'Failed to lookup pipelines for {0}: {1}'.format(self.app_name, resp.text)

        return resp.json()

    def compare_with_existing(self, region='us-east-1', onetime=False):
        """Compare desired pipeline with existing pipelines.

        Args:
            region (str): Region of desired pipeline.
            onetime (bool): Looks for different pipeline if Onetime

        Returns:
            str: pipeline_id if existing, empty string of not.

        """
        pipelines = self.get_existing_pipelines()
        pipeline_id = None
        found = False
        for pipeline in pipelines:
            correct_app_and_region = (pipeline['application'] == self.app_name) and (region in pipeline['name'])
            if onetime:
                onetime_str = "(onetime-{})".format(self.environments[0])
                if correct_app_and_region and onetime_str in pipeline['name']:
                    found = True
            elif correct_app_and_region:
                found = True

            if found:
                self.log.info('Existing pipeline found - %s', pipeline['name'])
                pipeline_id = pipeline['id']
                break
        else:
            self.log.info('No existing pipeline found')

        return pipeline_id

    def before_iter_region_env_yields(self, pipeline_vars):
        """Pipeline hook for before yielding :obj:`PipelineVars`."""
        self.generated.data.update({
            'env': pipeline_vars.env,
            'next_env': pipeline_vars.next_env,
            'region': pipeline_vars.region,
        })
        return pipeline_vars

    def after_iter_region_env_yields(self, pipeline_vars):
        """Pipeline hook for after yielding :obj:`PipelineVars`."""
        return pipeline_vars

    def iter_region_env(self):
        """Iterate over Pipeline Environment and Region variables.

        Yields:
            PipelineVars: Pipeline Region and Environment names.

        """
        pipeline_envs = self.environments
        self.log.debug('Envs from pipeline.json: %s', pipeline_envs)

        is_ec2_pipeline = self.settings['pipeline']['type'] in EC2_PIPELINE_TYPES
        spinnaker_subnets = None
        if is_ec2_pipeline:
            spinnaker_subnets = get_subnets()

        next_env = None
        previous_env = None
        for index, env in enumerate(pipeline_envs):
            for region in self.settings[env]['regions']:
                try:
                    next_env = pipeline_envs[index + 1]
                except IndexError:
                    next_env = None

                region_subnets = None

                if is_ec2_pipeline:
                    try:
                        subnets = spinnaker_subnets[env][region]
                    except KeyError:
                        self.log.info('%s is not available for %s.', env, region)
                        continue
                    else:
                        region_subnets = {region: subnets}

                pipeline_vars = PipelineVars(
                    env=env,
                    is_ec2_pipeline=is_ec2_pipeline,
                    next_env=next_env,
                    previous_env=previous_env,
                    region=region,
                    region_subnets=region_subnets,
                )

                pipeline_vars = self.before_iter_region_env_yields(pipeline_vars=pipeline_vars)
                yield pipeline_vars
                self.after_iter_region_env_yields(pipeline_vars=pipeline_vars)

                previous_env = env

    def assemble_pipelines(self):
        """Return rendered Pipeline per Region."""
        pipelines = {}

        for pipeline_vars in self.iter_region_env():
            if pipeline_vars.region not in pipelines:
                pipelines[pipeline_vars.region] = self.render_wrapper(region=pipeline_vars.region)

            pipeline_block_data = {
                'env': pipeline_vars.env,
                'generated': self.generated,
                'pipeline_data': self.settings['pipeline'],
                'previous_env': pipeline_vars.previous_env,
                'region': pipeline_vars.region,
                'region_subnets': pipeline_vars.region_subnets,
                'settings': self.settings[pipeline_vars.env][pipeline_vars.region],
            }

            block = construct_pipeline_block(**pipeline_block_data)
            pipelines[pipeline_vars.region]['stages'].extend(json.loads(block))

        self.log.debug('Assembled Pipelines:\n%s', pformat(pipelines))

        return pipelines

    def create_pipeline(self):
        """Entry point for pipeline creation."""
        pipelines = self.assemble_pipelines()
        clean_pipelines(app=self.app_name, settings=self.settings)

        for __region, pipeline in pipelines.items():
            self.post_pipeline(pipeline)

        return pipelines
