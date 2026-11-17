"""Repair only the four company-site A/AAAA aliases; preserve mail and all other DNS."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3

ACCOUNT = "616551057455"
ZONE = "Z06469251192ERBYD7UA2"
DOMAIN = "jatayulabs.com"
OLD_DISTRIBUTION = "d1htn4b53zwi2u.cloudfront.net"
WEB_KEYS = {(name, kind) for name in (DOMAIN + ".", "www." + DOMAIN + ".") for kind in ("A", "AAAA")}


def records(client):
    return [item for page in client.get_paginator("list_resource_record_sets").paginate(HostedZoneId=ZONE) for item in page["ResourceRecordSets"]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    session = boto3.Session(region_name="ap-south-1")
    assert session.client("sts").get_caller_identity()["Account"] == ACCOUNT
    r53 = session.client("route53")
    assert r53.get_hosted_zone(Id=ZONE)["HostedZone"]["Name"] == DOMAIN + "."
    stack = session.client("cloudformation").describe_stacks(StackName="companion-landing")["Stacks"][0]
    assert stack["StackStatus"] in {"CREATE_COMPLETE", "UPDATE_COMPLETE"}, stack["StackStatus"]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    target = outputs["DistributionDomain"].rstrip(".")
    dist = session.client("cloudfront").get_distribution(Id=outputs["DistributionId"])["Distribution"]
    assert dist["Status"] == "Deployed"
    assert dist["DistributionConfig"]["Enabled"]
    assert sorted(dist["DistributionConfig"]["Aliases"]["Items"]) == [DOMAIN, "www." + DOMAIN]
    assert dist["DomainName"] == target
    before = records(r53)
    existing = {(record["Name"], record["Type"]): record for record in before}
    for key in WEB_KEYS:
        current = existing.get(key)
        if current:
            alias = current.get("AliasTarget")
            assert alias and alias["DNSName"].rstrip(".") in {OLD_DISTRIBUTION, target}, f"Unreviewed DNS owner for {key}"
    desired = [{"Name": name, "Type": kind, "AliasTarget": {"HostedZoneId": "Z2FDTNDATAQYW2", "DNSName": target + ".", "EvaluateTargetHealth": False}} for name, kind in sorted(WEB_KEYS)]
    changes = [{"Action": "UPSERT", "ResourceRecordSet": record} for record in desired if existing.get((record["Name"], record["Type"])) != record]
    plan = {"account": ACCOUNT, "zone": ZONE, "distribution": outputs["DistributionId"], "changes": changes, "other_records_preserved": len(before) - sum(key in existing for key in WEB_KEYS)}
    print(json.dumps(plan, indent=2))
    if not args.apply or not changes:
        return
    Path("/tmp/jatayu-dns-before.json").write_text(json.dumps(before, indent=2))
    response = r53.change_resource_record_sets(HostedZoneId=ZONE, ChangeBatch={"Comment": "Restore Jatayu Labs company website; preserve all mail and product DNS", "Changes": changes})
    r53.get_waiter("resource_record_sets_changed").wait(Id=response["ChangeInfo"]["Id"])
    after = records(r53)
    protected_before = [record for record in before if (record["Name"], record["Type"]) not in WEB_KEYS]
    protected_after = [record for record in after if (record["Name"], record["Type"]) not in WEB_KEYS]
    assert protected_before == protected_after, "Non-website DNS changed during cutover; investigate immediately"
    print("DNS cutover is INSYNC. Mail, verification, NS, and other subdomain records are unchanged.")


if __name__ == "__main__":
    main()
