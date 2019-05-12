"""Kinesis Stream functions."""
import logging

import boto3

from ..exceptions import KinesisStreamNotFound

LOG = logging.getLogger(__name__)


def get_kinesis_stream_arn(stream_name, account, region):
    """Get Kinesis stream ARN.

    Args:
        stream_name (str): Name of the kinesis stream to lookup.
        account (str): Environment, e.g. dev
        region (str): Region name, e.g. us-east-1

    Returns:
        str: ARN for requested topic name

    """
    if stream_name.count(':') == 5 and stream_name.startswith('arn:aws:kinesis:'):
        return stream_name
    session = boto3.Session(profile_name=account, region_name=region)
    kinesis_client = session.client('kinesis')

    try:
        kinesis_stream = kinesis_client.describe_stream(StreamName=stream_name)
    except:
        LOG.critical("No kinesis stream with name %s found.", stream_name)
        raise KinesisStreamNotFound('No kinesis stream with name {0} found'.format(stream_name))
    
    stream_arn = kinesis_stream['StreamDescription']['StreamARN']
    
    return stream_arn