#!/usr/bin/env python3
"""EKS Alert Diagnostic — Report EC2-level health and metrics for EKS worker nodes."""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

REGIONS = ['cn-north-1', 'cn-northwest-1']

PERIOD_5MIN = 300
PERIOD_1HOUR = 3600


_SESSION_CACHE = {}


def _session(profile=None):
    if profile not in _SESSION_CACHE:
        _SESSION_CACHE[profile] = boto3.Session(profile_name=profile) if profile else boto3.Session()
    return _SESSION_CACHE[profile]


def _get_name_from_tags(tags):
    for tag in tags or []:
        if tag.get('Key') == 'Name':
            return tag.get('Value', '')
    return ''


def _status_flag(cpu_avg, health):
    if health.get('SystemStatus') != 'ok' or health.get('InstanceStatus') != 'ok':
        return '🔴'
    if cpu_avg is None:
        return '🟢'
    if cpu_avg > 80:
        return '🔴'
    if cpu_avg > 50:
        return '🟡'
    return '🟢'


def get_cluster_info(cluster_name, profile=None):
    """Validate cluster exists in either China region. Returns (region, cluster_info) or None."""
    session = _session(profile)
    for region in REGIONS:
        try:
            eks = session.client('eks', region_name=region)
            resp = eks.describe_cluster(name=cluster_name)
            return region, resp['cluster']
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
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
                name = _get_name_from_tags(inst.get('Tags'))
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
    end = datetime.now(timezone.utc)

    def _fetch(metric_name, start, period):
        try:
            resp = cw.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName=metric_name,
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=['Average', 'Maximum', 'Minimum'],
            )
        except ClientError as e:
            print(f'Warning: CloudWatch metric error for {metric_name}: {e}')
            return []
        return sorted(resp.get('Datapoints', []), key=lambda x: x['Timestamp'])

    metrics = {}
    for name in ['CPUUtilization', 'NetworkIn', 'NetworkOut', 'StatusCheckFailed']:
        metrics[f'{name}_1h'] = _fetch(name, end - timedelta(hours=1), PERIOD_5MIN)
        metrics[f'{name}_24h'] = _fetch(name, end - timedelta(hours=24), PERIOD_1HOUR)
    return metrics


def _safe_avg(points, key='Average'):
    if not points:
        return None
    return sum(p[key] for p in points) / len(points)


def _safe_max(points, key='Maximum'):
    if not points:
        return None
    return max(p[key] for p in points)


def _safe_min(points, key='Minimum'):
    if not points:
        return None
    return min(p[key] for p in points)


def plot_node_metrics(instance, metrics, output_dir):
    """Generate per-node CPU + network trend chart. Returns output path or None if no CPU data."""
    cpu_points = metrics.get('CPUUtilization_24h', [])
    if not cpu_points:
        return None

    timestamps = [p['Timestamp'] for p in cpu_points]
    cpu_avgs = [p['Average'] for p in cpu_points]

    net_in = metrics.get('NetworkIn_24h', [])
    net_out = metrics.get('NetworkOut_24h', [])
    net_in_avgs = [p['Average'] / 1024 / 1024 for p in net_in] if net_in else []
    net_out_avgs = [p['Average'] / 1024 / 1024 for p in net_out] if net_out else []

    label = instance['Name'] or instance['InstanceId']
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    ax1.plot(timestamps, cpu_avgs, linewidth=1.5, color='#2196F3')
    ax1.set_ylabel('CPU %')
    ax1.set_ylim(0, max(105, max(cpu_avgs) + 5))
    ax1.axhline(y=80, color='red', linestyle=':', linewidth=0.8, alpha=0.5)
    ax1.set_title(f'Node Health — {label} ({instance["InstanceId"]})\n'
                  f'Type: {instance["InstanceType"]}  AZ: {instance["AvailabilityZone"]}', fontsize=11)
    ax1.grid(True, alpha=0.3)

    if net_in_avgs or net_out_avgs:
        if net_in_avgs:
            ax2.plot(timestamps, net_in_avgs, linewidth=1.5, label='NetworkIn', color='#4CAF50')
        if net_out_avgs:
            ax2.plot(timestamps, net_out_avgs, linewidth=1.5, label='NetworkOut', color='#FF9800')
        ax2.legend(loc='upper right', fontsize=8)
    ax2.set_ylabel('Network (MiB/s)')
    ax2.grid(True, alpha=0.3)

    plt.xticks(rotation=30, fontsize=8)
    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    fname = os.path.join(output_dir, f'{instance["InstanceId"]}_health.png')
    plt.savefig(fname, dpi=150)
    plt.close()
    return fname


def plot_cluster_summary(nodes_data, cluster_name, output_dir):
    """Generate cluster-wide summary chart. Returns output path or None if nodes_data is empty."""
    if not nodes_data:
        return None

    labels = [n['Name'] or n['InstanceId'] for n in nodes_data]
    cpu_avgs = [n.get('CPUAvg_1h', 0) or 0 for n in nodes_data]
    health_ok = [1 if n.get('SystemStatus') == 'ok' and n.get('InstanceStatus') == 'ok' else 0 for n in nodes_data]

    x = range(len(labels))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8))

    colors = ['#4CAF50' if v < 50 else '#FFC107' if v < 80 else '#F44336' for v in cpu_avgs]
    ax1.bar(x, cpu_avgs, color=colors)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax1.set_ylabel('CPU Avg % (1h)')
    ax1.axhline(y=80, color='red', linestyle=':', linewidth=0.8, alpha=0.5)
    ax1.set_title(f'Cluster Summary — {cluster_name}', fontsize=11)
    ax1.grid(True, alpha=0.3, axis='y')

    health_colors = ['#4CAF50' if v == 1 else '#F44336' for v in health_ok]
    ax2.bar(x, health_ok, color=health_colors)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax2.set_ylabel('Health OK')
    ax2.set_ylim(0, 1.2)
    ax2.set_title('EC2 Status Checks (System + Instance)', fontsize=11)

    plt.tight_layout()
    os.makedirs(output_dir, exist_ok=True)
    fname = os.path.join(output_dir, f'{cluster_name}_summary.png')
    plt.savefig(fname, dpi=150)
    plt.close()
    return fname


def _prompt(question):
    return input(question).strip()


def main():
    parser = argparse.ArgumentParser(description='EKS Alert Diagnostic')
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--output', default='eks_reports', help='Output directory (default: eks_reports)')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    cluster_name = _prompt('EKS cluster name: ')
    if not cluster_name:
        print('Cluster name is required.')
        sys.exit(1)

    print(f'Looking up cluster "{cluster_name}" in {", ".join(REGIONS)} ...')
    region, cluster_info = get_cluster_info(cluster_name, args.profile)
    if not cluster_info:
        print(f'Cluster "{cluster_name}" not found in any region.')
        sys.exit(1)

    print(f'Found cluster in {region}. Status: {cluster_info.get("status", "unknown")}')

    mode = _prompt('Report mode (node/cluster): ').lower()
    while mode not in ('node', 'cluster'):
        mode = _prompt('Please enter "node" or "cluster": ').lower()

    if mode == 'node':
        instance_id = _prompt('EC2 Instance ID (e.g., i-xxxxxxxxxxxxxxxxx): ').strip()
        if not instance_id:
            print('Instance ID is required for node-level report.')
            sys.exit(1)

        print(f'Fetching health and metrics for {instance_id} ...')
        health = get_instance_health(instance_id, region, args.profile)
        metrics = get_ec2_metrics(instance_id, region, args.profile)

        ec2 = _session(args.profile).client('ec2', region_name=region)
        try:
            inst_resp = ec2.describe_instances(InstanceIds=[instance_id])
            reservations = inst_resp.get('Reservations', [])
            if reservations and reservations[0].get('Instances'):
                inst = reservations[0]['Instances'][0]
                instance_info = {
                    'InstanceId': instance_id,
                    'Name': _get_name_from_tags(inst.get('Tags')),
                    'InstanceType': inst['InstanceType'],
                    'AvailabilityZone': inst['Placement']['AvailabilityZone'],
                }
            else:
                print('Warning: could not describe instance: no data returned')
                instance_info = {
                    'InstanceId': instance_id,
                    'Name': '',
                    'InstanceType': 'unknown',
                    'AvailabilityZone': 'unknown',
                }
        except ClientError as e:
            print(f'Warning: could not describe instance: {e}')
            instance_info = {
                'InstanceId': instance_id,
                'Name': '',
                'InstanceType': 'unknown',
                'AvailabilityZone': 'unknown',
            }

        chart = plot_node_metrics(instance_info, metrics, args.output)

        cpu_avg = _safe_avg(metrics.get('CPUUtilization_1h', []))
        status_flag = _status_flag(cpu_avg, health)

        print(f'\n{"="*80}')
        print(f'  EKS Node Report')
        print(f'{"="*80}')
        print(f'  Instance:      {instance_id}')
        print(f'  Name:          {instance_info["Name"] or "-"}')
        print(f'  Type:          {instance_info["InstanceType"]}')
        print(f'  AZ:            {instance_info["AvailabilityZone"]}')
        print(f'  System Status: {health["SystemStatus"]}')
        print(f'  Instance Status: {health["InstanceStatus"]}')
        if cpu_avg is not None:
            print(f'  CPU Avg (1h):  {cpu_avg:.1f}%  {status_flag}')
        else:
            print('  CPU Avg (1h):  no data')
        if chart:
            print(f'  Chart:         {chart}')
        print(f'{"="*80}')
        return

    # cluster mode
    print('Discovering managed node groups ...')
    instances = get_nodegroup_instances(cluster_name, region, args.profile)
    if not instances:
        print('No managed node group instances found.')
        sys.exit(0)

    print(f'Found {len(instances)} instance(s)\n')
    results = []
    for inst in instances:
        iid = inst['InstanceId']
        label = inst['Name'] or iid
        print(f'  [{inst["NodegroupName"]}] {label} ({inst["InstanceType"]}) ... ', end='', flush=True)
        health = get_instance_health(iid, region, args.profile)
        metrics = get_ec2_metrics(iid, region, args.profile)
        chart = plot_node_metrics(inst, metrics, args.output)

        cpu_avg = _safe_avg(metrics.get('CPUUtilization_1h', []))
        net_in_avg = _safe_avg(metrics.get('NetworkIn_1h', []))
        status_flag = _status_flag(cpu_avg, health)

        inst['SystemStatus'] = health['SystemStatus']
        inst['InstanceStatus'] = health['InstanceStatus']
        inst['CPUAvg_1h'] = cpu_avg
        inst['NetworkInAvg_1h'] = net_in_avg
        inst['Chart'] = chart
        inst['Flag'] = status_flag
        results.append(inst)
        if cpu_avg is not None:
            print(f'CPU={cpu_avg:.1f}%  Health={health["SystemStatus"]}/{health["InstanceStatus"]}  {status_flag}')
        else:
            print('no data')

    summary_chart = plot_cluster_summary(results, cluster_name, args.output)

    print(f'\n{"="*110}')
    print(f'  EKS Cluster Report — {cluster_name}  ({region})')
    print(f'{"="*110}')
    print(f'  {"Instance ID":<22} {"Nodegroup":<18} {"Type":<12} {"AZ":<14} {"Sys":<8} {"Inst":<8} {"CPU%":>8} {"Flag":>6}')
    print(f'  {"-"*110}')
    for r in results:
        cpu_str = f'{r["CPUAvg_1h"]:.1f}' if r.get('CPUAvg_1h') is not None else '-'
        print(f'  {r["InstanceId"]:<22} {r["NodegroupName"]:<18} {r["InstanceType"]:<12} {r["AvailabilityZone"]:<14} '
              f'{r["SystemStatus"]:<8} {r["InstanceStatus"]:<8} {cpu_str:>8} {r["Flag"]:>6}')
    print(f'{"="*110}')
    if summary_chart:
        print(f'\nSummary chart: {summary_chart}')
    print(f'Charts saved to: {os.path.abspath(args.output)}/')


if __name__ == '__main__':
    main()
