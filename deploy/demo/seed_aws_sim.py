"""Seed the AWS simulator (moto) with a realistic multi-region estate for demos.

Runs once in the demo overlay. Creates, per region: a VPC with public/private subnets,
tiered security groups (ALB -> web -> app -> db references), an ALB with targets, and
a mix of Linux/Windows, x86/arm, on-demand instances with extra EBS volumes.
"""

import os
import time

import boto3

ENDPOINT = os.environ["AWS_ENDPOINT_URL"]
ESTATE = {
    "eu-central-1": {"web": ("m6i.large", 4), "app": ("m6i.2xlarge", 3), "db": ("r6i.4xlarge", 2),
                     "batch": ("c7g.2xlarge", 2)},
    "eu-west-1": {"web": ("t3.medium", 2), "app": ("m5.xlarge", 2), "db": ("r5.2xlarge", 1)},
    "us-east-1": {"legacy": ("m4.xlarge", 2), "win": ("m5.2xlarge", 2)},
}


def wait() -> None:
    for _ in range(60):
        try:
            boto3.client("sts", region_name="us-east-1").get_caller_identity()
            return
        except Exception:
            time.sleep(1)
    raise SystemExit("aws-sim not reachable")


def seed_region(region: str, tiers: dict[str, tuple[str, int]]) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    elb = boto3.client("elbv2", region_name=region)
    if any(v.get("Tags") for v in ec2.describe_vpcs()["Vpcs"] if not v.get("IsDefault")):
        print(f"{region}: already seeded")
        return
    tag = lambda kv: [{"ResourceType": "instance", "Tags": [{"Key": k, "Value": v} for k, v in kv.items()]}]  # noqa: E731
    vpc = ec2.create_vpc(CidrBlock="10.20.0.0/16")["Vpc"]["VpcId"]
    ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": f"prod-{region}"}])
    azs = [z["ZoneName"] for z in ec2.describe_availability_zones()["AvailabilityZones"]][:2]
    pub = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.20.{i}.0/24", AvailabilityZone=az)["Subnet"]["SubnetId"]
           for i, az in enumerate(azs)]
    priv = [ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.20.{10 + i}.0/24", AvailabilityZone=az)["Subnet"]["SubnetId"]
            for i, az in enumerate(azs)]
    for s, n in zip(pub + priv, ["public-a", "public-b", "private-a", "private-b"], strict=False):
        ec2.create_tags(Resources=[s], Tags=[{"Key": "Name", "Value": n}])

    sgs = {}
    for name in ("alb", "web", "app", "db", "admin"):
        sgs[name] = ec2.create_security_group(GroupName=f"{name}-sg", Description=f"{name} tier", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sgs["alb"], IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "HTTPS"}]}])
    for src, dst, port in (("alb", "web", 8080), ("web", "app", 8443), ("app", "db", 5432)):
        ec2.authorize_security_group_ingress(GroupId=sgs[dst], IpPermissions=[
            {"IpProtocol": "tcp", "FromPort": port, "ToPort": port, "UserIdGroupPairs": [{"GroupId": sgs[src]}]}])
    ec2.authorize_security_group_ingress(GroupId=sgs["admin"], IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "10.0.0.0/8"}]},
        {"IpProtocol": "tcp", "FromPort": 3389, "ToPort": 3389, "IpRanges": [{"CidrIp": "10.0.0.0/8"}]}])

    images = ec2.describe_images(Owners=["amazon"])["Images"]
    linux = next((i["ImageId"] for i in images if "ubuntu" in (i.get("Name") or "").lower()), images[0]["ImageId"])
    windows = next((i["ImageId"] for i in images if "windows" in (i.get("Name") or "").lower()), linux)

    web_ids: list[str] = []
    for tier, (itype, count) in tiers.items():
        sg = sgs.get(tier, sgs["app"])
        subnet = pub[0] if tier == "web" else priv[0]
        image = windows if tier == "win" else linux
        res = ec2.run_instances(ImageId=image, InstanceType=itype, MinCount=count, MaxCount=count, SubnetId=subnet,
                                SecurityGroupIds=[sg, sgs["admin"]],
                                TagSpecifications=tag({"Name": f"{tier}-{region}", "tier": tier, "env": "prod",
                                                       "owner": f"team-{tier}", "cost-center": "cc-1042"}))
        ids = [i["InstanceId"] for i in res["Instances"]]
        for n, iid in enumerate(ids):
            ec2.create_tags(Resources=[iid], Tags=[{"Key": "Name", "Value": f"{tier}-{region[-1]}{n + 1:02d}"}])
            if tier in ("db", "app", "legacy"):
                vol = ec2.create_volume(Size=500 if tier == "db" else 200, AvailabilityZone=azs[0], VolumeType="gp3",
                                        Iops=6000 if tier == "db" else 3000, Encrypted=tier == "db")["VolumeId"]
                ec2.attach_volume(VolumeId=vol, InstanceId=iid, Device="/dev/sdf")
        if tier == "web":
            web_ids = ids
    if web_ids:
        lb = elb.create_load_balancer(Name=f"web-{region}", Subnets=pub, SecurityGroups=[sgs["alb"]])["LoadBalancers"][0]
        tg = elb.create_target_group(Name=f"web-{region}", Protocol="HTTP", Port=8080, VpcId=vpc, TargetType="instance")[
            "TargetGroups"][0]
        elb.register_targets(TargetGroupArn=tg["TargetGroupArn"], Targets=[{"Id": i} for i in web_ids])
        elb.create_listener(LoadBalancerArn=lb["LoadBalancerArn"], Protocol="HTTP", Port=80,
                            DefaultActions=[{"Type": "forward", "TargetGroupArn": tg["TargetGroupArn"]}])
    print(f"{region}: seeded {sum(c for _, c in tiers.values())} instances")


def main() -> None:
    wait()
    iam = boto3.client("iam", region_name="us-east-1")
    for region, tiers in ESTATE.items():
        seed_region(region, tiers)
    # A demo user whose key is entered in the UI as an access-key connection.
    try:
        iam.create_user(UserName="aether-demo")
        key = iam.create_access_key(UserName="aether-demo")["AccessKey"]
        print(f"demo access key id: {key['AccessKeyId']}")
        print(f"demo secret access key: {key['SecretAccessKey']}")
    except iam.exceptions.EntityAlreadyExistsException:
        print("demo user exists (moto accepts any credentials; use any AKIA-format key)")


if __name__ == "__main__":
    main()
