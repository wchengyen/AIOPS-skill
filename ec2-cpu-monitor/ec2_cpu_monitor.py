#!/usr/bin/env python3
"""EC2 CPU Utilization Monitor - Scan cn-north-1 & cn-northwest-1, plot weekly CPU trends."""

import argparse
import os
from datetime import datetime, timedelta
import boto3
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

REGIONS = ['cn-north-1', 'cn-northwest-1']


def get_instances(profile=None):
    """Get all running EC2 instances across target regions."""
    instances = []
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    for region in REGIONS:
        ec2 = session.client('ec2', region_name=region)
        paginator = ec2.get_paginator('describe_instances')
        for page in paginator.paginate(Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]):
            for res in page['Reservations']:
                for inst in res['Instances']:
                    name = ''
                    for tag in inst.get('Tags', []):
                        if tag['Key'] == 'Name':
                            name = tag['Value']
                            break
                    instances.append({
                        'InstanceId': inst['InstanceId'],
                        'InstanceType': inst['InstanceType'],
                        'Region': region,
                        'Name': name,
                    })
    return instances


def get_cpu_metrics(instance_id, region, profile=None):
    """Query CloudWatch CPUUtilization for past 7 days, hourly."""
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    cw = session.client('cloudwatch', region_name=region)
    end = datetime.utcnow()
    start = end - timedelta(days=7)

    resp = cw.get_metric_statistics(
        Namespace='AWS/EC2',
        MetricName='CPUUtilization',
        Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
        StartTime=start, EndTime=end,
        Period=3600,
        Statistics=['Average', 'Maximum', 'Minimum'],
    )
    points = sorted(resp['Datapoints'], key=lambda x: x['Timestamp'])
    return points


def plot_cpu(instance, datapoints, output_dir):
    """Plot CPU trend chart and return stats."""
    if not datapoints:
        return None

    timestamps = [dp['Timestamp'] for dp in datapoints]
    avgs = [dp['Average'] for dp in datapoints]
    maxs = [dp['Maximum'] for dp in datapoints]
    mins = [dp['Minimum'] for dp in datapoints]

    overall_max = max(maxs)
    overall_min = min(mins)
    overall_avg = sum(avgs) / len(avgs)

    label = instance['Name'] or instance['InstanceId']
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(timestamps, avgs, linewidth=1.5, label='Avg', color='#2196F3')
    ax.fill_between(timestamps, mins, maxs, alpha=0.15, color='#2196F3')
    ax.plot(timestamps, maxs, linewidth=0.8, linestyle='--', label='Max', color='#F44336', alpha=0.7)
    ax.plot(timestamps, mins, linewidth=0.8, linestyle='--', label='Min', color='#4CAF50', alpha=0.7)

    ax.set_title(f'CPU Utilization - {label} ({instance["InstanceId"]})\n'
                 f'Region: {instance["Region"]}  Type: {instance["InstanceType"]}', fontsize=11)
    ax.set_ylabel('CPU %')
    ax.set_ylim(0, max(105, overall_max + 5))
    ax.axhline(y=80, color='red', linestyle=':', linewidth=0.8, alpha=0.5, label='80% threshold')
    ax.legend(loc='upper right', fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:00'))
    ax.xaxis.set_major_locator(mdates.DayLocator())
    plt.xticks(rotation=30, fontsize=8)
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    fname = os.path.join(output_dir, f'{instance["InstanceId"]}_cpu.png')
    plt.savefig(fname, dpi=150)
    plt.close()

    return {'max': overall_max, 'min': overall_min, 'avg': overall_avg, 'chart': fname}


def main():
    parser = argparse.ArgumentParser(description='EC2 CPU Utilization Monitor')
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--output', default='cpu_reports', help='Output directory (default: cpu_reports)')
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    print(f'Scanning EC2 instances in {", ".join(REGIONS)} ...')
    instances = get_instances(args.profile)
    print(f'Found {len(instances)} running instance(s)\n')

    if not instances:
        print('No running instances found.')
        return

    results = []
    for inst in instances:
        iid = inst['InstanceId']
        label = inst['Name'] or iid
        print(f'  [{inst["Region"]}] {label} ({inst["InstanceType"]}) ... ', end='', flush=True)
        points = get_cpu_metrics(iid, inst['Region'], args.profile)
        if not points:
            print('no data')
            continue
        stats = plot_cpu(inst, points, args.output)
        results.append({**inst, **stats})
        print(f'Max={stats["max"]:.1f}%  Min={stats["min"]:.1f}%  Avg={stats["avg"]:.1f}%')

    # Summary
    print(f'\n{"="*90}')
    print(f'{"Instance ID":<22} {"Name":<20} {"Type":<14} {"Region":<16} {"Max%":>6} {"Min%":>6} {"Avg%":>6}')
    print(f'{"-"*90}')
    for r in sorted(results, key=lambda x: x['avg'], reverse=True):
        name = (r['Name'] or '-')[:18]
        print(f'{r["InstanceId"]:<22} {name:<20} {r["InstanceType"]:<14} {r["Region"]:<16} {r["max"]:>5.1f}% {r["min"]:>5.1f}% {r["avg"]:>5.1f}%')

    # Alerts
    high = [r for r in results if r['avg'] > 80]
    low = [r for r in results if r['avg'] < 5]
    if high:
        print(f'\n🔴 High CPU (Avg>80%): {", ".join(r["InstanceId"] for r in high)}')
    if low:
        print(f'\n🟡 Low CPU (Avg<5%): {", ".join(r["InstanceId"] for r in low)}')

    print(f'\nCharts saved to: {os.path.abspath(args.output)}/')


if __name__ == '__main__':
    main()
