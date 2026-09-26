"""Migration assessment rules engine (PROJECT_PLAN §14). Pure: no I/O, no LLM.

A rule is a function ``(ctx) -> list[Finding]`` registered with metadata. Rules read the
normalised inventory (plus its graph neighbourhood) and the sizing result, and every
finding carries the evidence it was based on and a concrete remediation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from aether.core.inventory import SecurityRule, VmSpec
from aether.sizing.engine import SizingResult

RULESET_VERSION = "2026.09.1"


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"


class Finding(BaseModel):
    rule_id: str
    rule_version: int
    severity: Severity
    category: str
    title: str
    message: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str
    acknowledged: bool = False
    acknowledgement: dict[str, Any] | None = None


class Readiness(StrEnum):
    READY = "ready"
    READY_WITH_CHANGES = "ready_with_changes"
    BLOCKED = "blocked"


class VmAssessment(BaseModel):
    resource_id: str
    native_id: str
    name: str | None
    readiness: Readiness
    score: int
    findings: list[Finding]


@dataclass
class Context:
    vm: VmSpec
    native_id: str
    name: str | None
    source_provider: str
    target_provider: str
    target_region: str
    sizing: SizingResult | None
    tags: dict[str, str] = field(default_factory=dict)
    security_groups: list[dict[str, Any]] = field(default_factory=list)  # {native_id, name, rules: [...]}
    load_balancers: list[dict[str, Any]] = field(default_factory=list)  # {native_id, name, kind, scheme}
    today: date = field(default_factory=date.today)


@dataclass(frozen=True)
class Rule:
    id: str
    version: int
    category: str
    fn: Callable[[Context], list[Finding]]


RULES: list[Rule] = []


def rule(rule_id: str, category: str, version: int = 1) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        RULES.append(Rule(rule_id, version, category, fn))
        return fn

    return deco


def _f(ctx_rule: str, sev: Severity, title: str, message: str, remediation: str, **evidence: Any) -> Finding:
    r = next(r for r in RULES if r.id == ctx_rule)
    return Finding(
        rule_id=r.id,
        rule_version=r.version,
        severity=sev,
        category=r.category,
        title=title,
        message=message,
        evidence=evidence,
        remediation=remediation,
    )


# --------------------------------------------------------------------------- reference data

# End of standard support (vendor), used for OS-002. Extended/paid support is noted separately.
OS_EOL: dict[tuple[str, str], tuple[date, str | None]] = {
    ("ubuntu", "14.04"): (date(2019, 4, 30), "ESM ended 2024-04"),
    ("ubuntu", "16.04"): (date(2021, 4, 30), "ESM until 2026-04"),
    ("ubuntu", "18.04"): (date(2023, 5, 31), "ESM until 2028-04"),
    ("ubuntu", "20.04"): (date(2025, 5, 31), "ESM until 2030-04"),
    ("ubuntu", "22.04"): (date(2027, 6, 1), None),
    ("ubuntu", "24.04"): (date(2029, 5, 31), None),
    ("centos", "6"): (date(2020, 11, 30), None),
    ("centos", "7"): (date(2024, 6, 30), None),
    ("centos", "8"): (date(2021, 12, 31), None),
    ("rhel", "6"): (date(2020, 11, 30), "ELS ended 2024-06"),
    ("rhel", "7"): (date(2024, 6, 30), "ELS until 2028-06"),
    ("rhel", "8"): (date(2029, 5, 31), None),
    ("rhel", "9"): (date(2032, 5, 31), None),
    ("debian", "10"): (date(2024, 6, 30), None),
    ("debian", "11"): (date(2026, 8, 31), None),
    ("debian", "12"): (date(2028, 6, 30), None),
    ("sles", "12"): (date(2024, 10, 31), "LTSS until 2027-10"),
    ("sles", "15"): (date(2031, 7, 31), None),
    ("amazon-linux", "2"): (date(2026, 6, 30), None),
    ("amazon-linux", "2023"): (date(2029, 6, 30), None),
    ("windows-server", "2008"): (date(2020, 1, 14), None),
    ("windows-server", "2008 R2"): (date(2020, 1, 14), None),
    ("windows-server", "2012"): (date(2023, 10, 10), "ESU available on Azure"),
    ("windows-server", "2012 R2"): (date(2023, 10, 10), "ESU available on Azure"),
    ("windows-server", "2016"): (date(2027, 1, 12), None),
    ("windows-server", "2019"): (date(2029, 1, 9), None),
    ("windows-server", "2022"): (date(2031, 10, 14), None),
}
# Guest OS versions Azure does not support as VM guests.
AZURE_UNSUPPORTED: set[tuple[str, str]] = {
    ("windows-server", "2003"),
    ("centos", "5"),
    ("rhel", "5"),
    ("ubuntu", "12.04"),
}
ADMIN_PORTS = {22: "SSH", 3389: "RDP", 5985: "WinRM", 5986: "WinRM"}


def _major(distro: str, version: str) -> str:
    if distro in ("ubuntu", "windows-server", "amazon-linux"):
        return version
    return version.split(".")[0]


# --------------------------------------------------------------------------- rules


@rule("OS-001", "operating system")
def os_supported(ctx: Context) -> list[Finding]:
    vm = ctx.vm
    if vm.os_family == "unknown" or not vm.os_distribution:
        return [
            _f(
                "OS-001",
                Severity.WARNING,
                "Operating system not identified",
                "The guest OS could not be determined from platform details or the image name.",
                "Confirm the OS and version in the guest (or tag the VM) before planning.",
                os_family=vm.os_family,
                image=vm.image_name,
            )
        ]
    key = (vm.os_distribution, _major(vm.os_distribution, vm.os_version or ""))
    if key in AZURE_UNSUPPORTED:
        return [
            _f(
                "OS-001",
                Severity.BLOCKER,
                "Guest OS not supported on Azure",
                f"{vm.os_distribution} {vm.os_version} is not a supported Azure guest.",
                "Upgrade the OS or re-platform the workload before migrating.",
                os=f"{key[0]} {key[1]}",
            )
        ]
    return []


@rule("OS-002", "operating system")
def os_eol(ctx: Context) -> list[Finding]:
    vm = ctx.vm
    if not vm.os_distribution or not vm.os_version:
        return []
    eol = OS_EOL.get((vm.os_distribution, _major(vm.os_distribution, vm.os_version)))
    if not eol:
        return []
    end, extended = eol
    if end <= ctx.today:
        return [
            _f(
                "OS-002",
                Severity.WARNING,
                "Operating system past end of standard support",
                f"{vm.os_distribution} {vm.os_version} reached end of standard support on {end.isoformat()}"
                + (f" ({extended})." if extended else "."),
                "Plan an OS upgrade before or during migration; do not carry unsupported OS versions forward.",
                os=f"{vm.os_distribution} {vm.os_version}",
                end_of_support=end.isoformat(),
                inferred=vm.provenance.get("os_version") == "inferred",
            )
        ]
    if (end - ctx.today).days < 365:
        return [
            _f(
                "OS-002",
                Severity.INFO,
                "Operating system support ends within a year",
                f"{vm.os_distribution} {vm.os_version} standard support ends {end.isoformat()}.",
                "Consider upgrading as part of the migration.",
                end_of_support=end.isoformat(),
            )
        ]
    return []


@rule("OS-003", "operating system")
def amazon_linux(ctx: Context) -> list[Finding]:
    if ctx.vm.os_distribution != "amazon-linux" or ctx.target_provider == "aws":
        return []
    return [
        _f(
            "OS-003",
            Severity.BLOCKER,
            "Amazon Linux is AWS-specific",
            "Amazon Linux images are built and supported for AWS; they are not offered or supported on Azure.",
            "Re-platform to a supported distribution (e.g. RHEL, Ubuntu, AlmaLinux) and redeploy the workload.",
            os=f"amazon-linux {ctx.vm.os_version or ''}".strip(),
        )
    ]


@rule("CPU-001", "compute")
def cpu_arch(ctx: Context) -> list[Finding]:
    if ctx.vm.cpu_arch != "arm64":
        return []
    matched = bool(ctx.sizing and ctx.sizing.candidates)
    if not matched:
        return [
            _f(
                "CPU-001",
                Severity.BLOCKER,
                "No arm64 target size",
                "The source runs on arm64 (Graviton) and no arm64 size is available in the target region.",
                "Choose a region with arm64 sizes (e.g. Dpsv5/Epsv5) or rebuild for x86_64.",
                cpu_arch="arm64",
            )
        ]
    return [
        _f(
            "CPU-001",
            Severity.INFO,
            "arm64 workload",
            "Graviton → Ampere Altra: images must be arm64 and boot as Generation 2 (UEFI).",
            "Use arm64 images; validate third-party agents for arm64.",
            cpu_arch="arm64",
        )
    ]


@rule("BOOT-001", "boot")
def boot_mode(ctx: Context) -> list[Finding]:
    mode = ctx.vm.boot_mode
    if mode == "unknown":
        return [
            _f(
                "BOOT-001",
                Severity.INFO,
                "Boot mode unknown",
                "The firmware boot mode could not be determined.",
                "Check /sys/firmware/efi (Linux) or msinfo32 (Windows); UEFI → Gen2 VM, BIOS → Gen1 VM.",
            )
        ]
    gen = "Generation 2 (UEFI)" if mode in ("uefi", "uefi_preferred") else "Generation 1 (BIOS)"
    return [
        _f(
            "BOOT-001",
            Severity.INFO,
            f"Create as {gen} VM",
            f"The source boots with {mode.upper().replace('_', ' ')}.",
            f"Import the OS disk as a {gen} image.",
            boot_mode=mode,
        )
    ]


@rule("BOOT-002", "boot")
def boot_disk_size(ctx: Context) -> list[Finding]:
    boot = next((d for d in ctx.vm.disks if d.boot and not d.ephemeral), None)
    if not boot or not boot.size_gib:
        return []
    if boot.size_gib > 4095:
        return [
            _f(
                "BOOT-002",
                Severity.BLOCKER,
                "OS disk larger than 4 TiB",
                f"The boot volume is {boot.size_gib} GiB; Azure OS disks are limited to 4 TiB.",
                "Move data off the OS disk to data disks before migrating.",
                size_gib=boot.size_gib,
            )
        ]
    if boot.size_gib > 2048 and ctx.vm.boot_mode == "bios":
        return [
            _f(
                "BOOT-002",
                Severity.BLOCKER,
                "BIOS boot disk larger than 2 TiB",
                "Generation 1 (BIOS/MBR) OS disks cannot exceed 2 TiB.",
                "Convert to GPT/UEFI (Gen2) or shrink the OS disk.",
                size_gib=boot.size_gib,
            )
        ]
    return []


@rule("DRV-001", "drivers")
def drivers(ctx: Context) -> list[Finding]:
    if ctx.source_provider != "aws" or ctx.target_provider != "azure":
        return []
    if ctx.vm.os_family == "windows":
        msg = (
            "AWS instances use ENA/NVMe and AWS PV drivers; Azure uses Hyper-V synthetic devices. Windows "
            "Server includes Hyper-V integration services, but AWS drivers and EC2Launch should be removed."
        )
        fix = "Uninstall AWS PV/ENA drivers and EC2Launch/EC2Config after cutover; verify the Azure VM Agent."
    else:
        msg = (
            "The kernel must load Hyper-V drivers (hv_vmbus, hv_storvsc, hv_netvsc) at boot; images built for "
            "AWS often omit them from the initramfs."
        )
        fix = "Add Hyper-V modules to the initramfs (dracut/update-initramfs) and install the Azure Linux Agent."
    return [
        _f(
            "DRV-001",
            Severity.WARNING,
            "Hypervisor driver changes required",
            msg,
            fix,
            os_family=ctx.vm.os_family,
            hypervisor=ctx.vm.hypervisor,
        )
    ]


@rule("DISK-001", "storage")
def ephemeral(ctx: Context) -> list[Finding]:
    eph = [d for d in ctx.vm.disks if d.ephemeral]
    if not eph:
        return []
    return [
        _f(
            "DISK-001",
            Severity.WARNING,
            "Instance-store data will not migrate",
            f"{sum(d.size_gib or 0 for d in eph)} GiB of instance-store (ephemeral) disk is lost on stop/migration.",
            "Confirm the data is disposable (cache/scratch) or copy it to a managed disk first.",
            disks=[d.device for d in eph],
        )
    ]


@rule("DISK-002", "storage")
def disk_limits(ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    for d in ctx.vm.disks:
        if d.ephemeral:
            continue
        if (d.size_gib or 0) > 32767:
            out.append(
                _f(
                    "DISK-002",
                    Severity.BLOCKER,
                    "Disk larger than 32 TiB",
                    f"{d.device}: {d.size_gib} GiB exceeds the 32 TiB managed-disk maximum.",
                    "Split the volume (LVM/Storage Spaces) across multiple managed disks.",
                    device=d.device,
                    size_gib=d.size_gib,
                )
            )
        if (d.iops or 0) > 20000 and (d.type_class or "") in ("io1", "io2", "gp3"):
            out.append(
                _f(
                    "DISK-002",
                    Severity.WARNING,
                    "High-IOPS volume",
                    f"{d.device} is provisioned for {d.iops} IOPS, above Premium SSD P80 (20,000).",
                    "Target Premium SSD v2 or Ultra Disk and re-price.",
                    device=d.device,
                    iops=d.iops,
                )
            )
    return out


@rule("DISK-003", "storage")
def encryption(ctx: Context) -> list[Finding]:
    keyed = [d.device for d in ctx.vm.disks if d.kms_key_ref]
    if not keyed:
        return []
    return [
        _f(
            "DISK-003",
            Severity.WARNING,
            "Customer-managed encryption keys",
            "Volumes are encrypted with AWS KMS keys, which cannot be used on Azure.",
            "Create keys in Azure Key Vault and a Disk Encryption Set (or accept platform-managed keys).",
            devices=keyed,
        )
    ]


@rule("NET-001", "network")
def ip_change(ctx: Context) -> list[Finding]:
    private = [ip for n in ctx.vm.nics for ip in n.private_ips]
    public = [ip for n in ctx.vm.nics for ip in n.public_ips]
    out = [
        _f(
            "NET-001",
            Severity.WARNING,
            "IP addresses will change",
            "Private IPs are allocated from the target VNet; anything hard-coding the current addresses breaks.",
            "Inventory hard-coded IPs (configs, allow-lists, DNS) and switch to DNS names before cutover.",
            private_ips=private,
        )
    ]
    if public:
        out.append(
            _f(
                "NET-001",
                Severity.INFO,
                "Public IP / DNS cutover",
                "The VM has public IP addresses that cannot move between clouds.",
                "Lower DNS TTLs ahead of cutover and update DNS records to the new public IPs.",
                public_ips=public,
            )
        )
    return out


def _rules(sg: dict[str, Any]) -> list[SecurityRule]:
    return [SecurityRule.model_validate(r) for r in sg.get("rules") or []]


@rule("NET-002", "network")
def sg_translation(ctx: Context) -> list[Finding]:
    refs = sorted(
        {r.peer_group_native_id for sg in ctx.security_groups for r in _rules(sg) if r.peer_group_native_id}
    )
    if not refs:
        return []
    return [
        _f(
            "NET-002",
            Severity.WARNING,
            "Security-group references need translation",
            "Rules reference other security groups; Azure NSGs express this with Application Security Groups.",
            "Create an ASG per referenced group and rewrite the rules; the planner generates this mapping.",
            referenced_groups=refs,
        )
    ]


@rule("NET-003", "network")
def open_admin(ctx: Context) -> list[Finding]:
    exposed = []
    for sg in ctx.security_groups:
        for r in _rules(sg):
            if r.direction != "ingress" or r.peer_cidr not in ("0.0.0.0/0", "::/0"):
                continue
            for port, name in ADMIN_PORTS.items():
                if r.protocol == "all" or (
                    r.port_from is not None and r.port_from <= port <= (r.port_to or port)
                ):
                    exposed.append(f"{name} ({port}) via {sg.get('name') or sg.get('native_id')}")
    if not exposed:
        return []
    return [
        _f(
            "NET-003",
            Severity.WARNING,
            "Admin ports open to the internet",
            "Management ports are reachable from 0.0.0.0/0; do not carry this exposure into the target.",
            "Use Azure Bastion or Just-in-Time access and restrict NSG sources.",
            exposed=sorted(set(exposed)),
        )
    ]


@rule("ID-001", "identity")
def instance_identity(ctx: Context) -> list[Finding]:
    if not ctx.vm.instance_identity:
        return []
    return [
        _f(
            "ID-001",
            Severity.WARNING,
            "Workload uses an instance role",
            "The VM uses an IAM instance profile; applications calling AWS APIs lose their credentials.",
            "Map required permissions to an Azure Managed Identity, or keep cross-cloud access explicitly.",
            instance_profile=ctx.vm.instance_identity,
        )
    ]


@rule("LIC-001", "licensing")
def licensing(ctx: Context) -> list[Finding]:
    details = (ctx.vm.platform_details or "").lower()
    if ctx.vm.license_model != "included" and "sql" not in details:
        return []
    return [
        _f(
            "LIC-001",
            Severity.INFO,
            "Licence-included software",
            f"The source is billed with licence included ({ctx.vm.platform_details}).",
            "Decide between licence-included pricing and Azure Hybrid Benefit / BYOS; re-price accordingly.",
            platform_details=ctx.vm.platform_details,
        )
    ]


@rule("DEP-001", "dependencies")
def load_balanced(ctx: Context) -> list[Finding]:
    if not ctx.load_balancers:
        return []
    names = [lb.get("name") or lb.get("native_id") for lb in ctx.load_balancers]
    return [
        _f(
            "DEP-001",
            Severity.WARNING,
            "Member of a load-balanced pool",
            f"The VM receives traffic from {', '.join(map(str, names))}; members must move together with the "
            "load balancer configuration.",
            "Migrate the pool as one wave; map ALB → Application Gateway, NLB → Azure Load Balancer.",
            load_balancers=names,
        )
    ]


@rule("TEN-001", "compute")
def tenancy(ctx: Context) -> list[Finding]:
    if ctx.vm.tenancy in (None, "default"):
        return []
    return [
        _f(
            "TEN-001",
            Severity.WARNING,
            "Dedicated tenancy",
            f"The source runs with '{ctx.vm.tenancy}' tenancy (often for licensing or compliance).",
            "Evaluate Azure Dedicated Host and re-price; confirm the compliance requirement.",
            tenancy=ctx.vm.tenancy,
        )
    ]


@rule("GPU-001", "compute")
def gpu(ctx: Context) -> list[Finding]:
    if not ctx.vm.gpu_count:
        return []
    return [
        _f(
            "GPU-001",
            Severity.BLOCKER,
            "GPU sizing needs manual review",
            f"{ctx.vm.gpu_count} x {ctx.vm.gpu_model}: GPU sizes are not in the priced catalog.",
            "Select an N-series size manually and confirm regional GPU quota.",
            gpu=f"{ctx.vm.gpu_count}x {ctx.vm.gpu_model}",
        )
    ]


@rule("SIZE-001", "compute")
def no_size(ctx: Context) -> list[Finding]:
    if ctx.sizing is None or ctx.sizing.candidates or ctx.vm.cpu_arch == "arm64" or ctx.vm.gpu_count:
        return []
    return [
        _f(
            "SIZE-001",
            Severity.BLOCKER,
            "No target size found",
            "; ".join(ctx.sizing.warnings) or "No target size satisfies the requirements.",
            "Choose another region or sizing strategy, or sync the catalog.",
        )
    ]


# --------------------------------------------------------------------------- engine


def assess(
    ctx: Context, native_resource_id: str, acknowledgements: dict[str, dict[str, Any]] | None = None
) -> VmAssessment:
    findings: list[Finding] = []
    for r in RULES:
        for f in r.fn(ctx):
            ack = (acknowledgements or {}).get(f.rule_id)
            if ack and f.severity != Severity.BLOCKER:
                f.acknowledged, f.acknowledgement = True, ack
            findings.append(f)
    order = {Severity.BLOCKER: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule_id))
    blockers = sum(f.severity == Severity.BLOCKER for f in findings)
    open_warnings = sum(f.severity == Severity.WARNING and not f.acknowledged for f in findings)
    score = 0 if blockers else max(0, 100 - 12 * open_warnings)
    readiness = (
        Readiness.BLOCKED
        if blockers
        else Readiness.READY
        if open_warnings == 0
        else Readiness.READY_WITH_CHANGES
    )
    return VmAssessment(
        resource_id=native_resource_id,
        native_id=ctx.native_id,
        name=ctx.name,
        readiness=readiness,
        score=score,
        findings=findings,
    )


def quota_needs(sizings: list[tuple[str, SizingResult | None]]) -> list[dict[str, Any]]:
    """Aggregate vCPU needed per target family (QUOTA-001): quotas are per family and region."""
    families: dict[str, int] = {}
    for _, s in sizings:
        if s and s.candidates:
            c = s.candidates[0]
            families[c.family] = families.get(c.family, 0) + c.vcpu
    return [{"family": f, "vcpu": v} for f, v in sorted(families.items(), key=lambda x: -x[1])]
