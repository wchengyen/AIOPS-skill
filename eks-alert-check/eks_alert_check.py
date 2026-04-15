#!/usr/bin/env python3
"""EKS Alert Diagnostic — Report EC2-level health and metrics for EKS worker nodes."""

import argparse
import os
import sys
from datetime import datetime, timedelta

import boto3
from botocore.exceptions import ClientError

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REGIONS = ['cn-north-1', 'cn-northwest-1']


def _session(profile=None):
    return boto3.Session(profile_name=profile) if profile else boto3.Session()


def get_cluster_info(cluster_name, profile=None):
    """Validate cluster exists in either China region. Returns (region, cluster_info) or None."""
    session = _session(profile)
    for region in REGIONS:
        try:
            eks = session.client('eks', region_name=region)
            resp = eks.describe_cluster(name=cluster_name)
            return region, resp['cluster']
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                continue
            raise
    return None, None


def get_nodegroup_instances(cluster_name, region, profile=None):
    """Map managed node groups to EC2 instances. Returns list of dicts."""
    session = _session(profile)
    eks = session.client('eks', region_name=region)
    autoscaling = session.client('autoscaling', region_name=region)
    ec2 = session.client('ec2', region_name=region)

    nodegroups_resp = eks.list_nodegroups(clusterName=cluster_name)
    nodegroups = nodegroups_resp.get('nodegroups', [])

    instances = []
    asg_instance_ids = []
    asg_to_nodegroup = {}

    for ng_name in nodegroups:
        ng = eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)['nodegroup']
        resources = ng.get('resources', {})
        asgs = resources.get('autoScalingGroups', [])
        for asg in asgs:
            asg_name = asg['name']
            asg_to_nodegroup[asg_name] = ng_name

    if not asg_to_nodegroup:
        return instances

    asg_resp = autoscaling.describe_auto_scaling_groups(AutoScalingGroupNames=list(asg_to_nodegroup.keys()))
    for asg in asg_resp.get('AutoScalingGroups', []):
        asg_name = asg['AutoScalingGroupName']
        for inst in asg.get('Instances', []):
            asg_instance_ids.append({
                'InstanceId': inst['InstanceId'],
                'AutoScalingGroupName': asg_name,
                'LifecycleState': inst['LifecycleState'],
                'NodegroupName': asg_to_nodegroup[asg_name],
            })

    if not asg_instance_ids:
        return instances

    ids = [i['InstanceId'] for i in asg_instance_ids]
    id_to_details = {}
    for chunk in [ids[i:i + 200] for i in range(0, len(ids), 200)]:
        resp = ec2.describe_instances(InstanceIds=chunk)
        for reservation in resp['Reservations']:
            for inst in reservation['Instances']:
                name = ''
                for tag in inst.get('Tags', []):
                    if tag['Key'] == 'Name':
                        name = tag['Value']
                        break
                id_to_details[inst['InstanceId']] = {
                    'InstanceType': inst['InstanceType'],
                    'AvailabilityZone': inst['Placement']['AvailabilityZone'],
                    'Name': name,
                }

    for mapping in asg_instance_ids:
        iid = mapping['InstanceId']
        details = id_to_details.get(iid, {})
        instances.append({
            'InstanceId': iid,
            'NodegroupName': mapping['NodegroupName'],
            'AutoScalingGroupName': mapping['AutoScalingGroupName'],
            'LifecycleState': mapping['LifecycleState'],
            'InstanceType': details.get('InstanceType', 'unknown'),
            'AvailabilityZone': details.get('AvailabilityZone', 'unknown'),
            'Name': details.get('Name', ''),
        })

    return instances


def get_instance_health(instance_id, region, profile=None):
    """Fetch EC2 instance status checks. Returns dict."""
    session = _session(profile)
    ec2 = session.client('ec2', region_name=region)
    try:
        resp = ec2.describe_instance_status(InstanceIds=[instance_id], IncludeAllInstances=True)
        statuses = resp.get('InstanceStatuses', [])
        if not statuses:
            return {'SystemStatus': 'unknown', 'InstanceStatus': 'unknown'}
        status = statuses[0]
        return {
            'SystemStatus': status.get('SystemStatus', {}).get('Status', 'unknown'),
            'InstanceStatus': status.get('InstanceStatus', {}).get('Status', 'unknown'),
        }
    except ClientError as e:
        print(f'Warning: instance health error: {e}')
        return {'SystemStatus': f'error: {e}', 'InstanceStatus': f'error: {e}'}


def get_ec2_metrics(instance_id, region, profile=None):
    """Query CloudWatch EC2 metrics for past 1h (5min) and 24h (1h). Returns dict."""
    session = _session(profile)
    cw = session.client('cloudwatch', region_name=region)
    end = datetime.utcnow()

    def _fetch(metric_name, start, period):
        resp = cw.get_metric_statistics(
            Namespace='AWS/EC2',
            MetricName=metric_name,
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            StartTime=start,
            EndTime=end,
            Period=period,
            Statistics=['Average', 'Maximum', 'Minimum'],
        )
        return sorted(resp.get('Datapoints', []), key=lambda x: x['Timestamp'])

    metrics = {}
    for name in ['CPUUtilization', 'NetworkIn', 'NetworkOut', 'StatusCheckFailed']:
        metrics[f'{name}_1h'] = _fetch(name, end - timedelta(hours=1), 300)
        metrics[f'{name}_24h'] = _fetch(name, end - timedelta(hours=24), 3600)
    return metrics


if __name__ == '__main__':
    pass
