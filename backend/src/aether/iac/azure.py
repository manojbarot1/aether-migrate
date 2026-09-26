"""OpenTofu generator for the Azure landing zone of a plan (PROJECT_PLAN §15). Pure.

Generates: resource group, VNets mirroring source VPC address spaces, subnets with the
same CIDRs, one NSG per source security group (rules translated), and an Application
Security Group for every security group referenced by another group's rules. VMs are
created by the migration tooling (Azure Migrate); the module outputs, per machine, the
subnet, NSG and ASGs its NIC must use.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from aether.core.inventory import SecurityRule

AZURERM_VERSION = "~> 4.0"
_PROTO = {"tcp": "Tcp", "udp": "Udp", "icmp": "Icmp", "icmpv6": "Icmp", "all": "*", "-1": "*"}


def ident(name: str, native_id: str) -> str:
    """Stable, collision-free HCL identifier."""
    base = re.sub(r"[^a-z0-9_]", "_", (name or native_id).lower()).strip("_")[:40] or "r"
    if base[0].isdigit():
        base = f"r_{base}"
    return f"{base}_{hashlib.sha1(native_id.encode()).hexdigest()[:6]}"  # noqa: S324 - naming, not security


def azname(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)[:80].strip("-.") or "unnamed"


def q(value: str) -> str:
    return json.dumps(value)  # HCL string literals are JSON-compatible for our character set


def _ports(r: SecurityRule) -> str:
    if r.port_from is None or r.protocol in ("all", "-1", "icmp", "icmpv6"):
        return "*"
    if r.port_from == r.port_to or r.port_to is None:
        return str(r.port_from)
    if r.port_from == 0 and r.port_to == 65535:
        return "*"
    return f"{r.port_from}-{r.port_to}"


def _prefix(cidr: str) -> str:
    return "*" if cidr in ("0.0.0.0/0", "::/0") else cidr


def translate_rules(sg: dict[str, Any], asg_refs: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (HCL security_rule blocks, notes about rules that could not be translated)."""
    rules = [SecurityRule.model_validate(r) for r in sg.get("rules") or []]
    blocks: list[str] = []
    notes: list[str] = []
    priority = {"ingress": 100, "egress": 100}
    egress = [r for r in rules if r.direction == "egress"]
    egress_is_default = any(
        r.protocol in ("all", "-1") and r.peer_cidr in ("0.0.0.0/0", "::/0") for r in egress
    )
    for i, r in enumerate(rules):
        if r.direction == "egress" and egress_is_default:
            continue  # Azure's default rules already allow all outbound traffic
        if r.peer_prefix_list:
            notes.append(
                f"{r.direction} rule to AWS prefix list {r.peer_prefix_list} has no Azure equivalent; "
                "replace with a service tag"
            )
            continue
        direction = "Inbound" if r.direction == "ingress" else "Outbound"
        remote = ""
        if r.peer_group_native_id:
            asg = asg_refs.get(r.peer_group_native_id)
            if not asg:
                notes.append(f"{r.direction} rule references {r.peer_group_native_id}, which is not in scope")
                continue
            key = (
                "source_application_security_group_ids"
                if direction == "Inbound"
                else "destination_application_security_group_ids"
            )
            remote = f"    {key} = [azurerm_application_security_group.{asg}.id]\n"
            local_prefix = (
                'destination_address_prefix = "*"'
                if direction == "Inbound"
                else 'source_address_prefix = "*"'
            )
        else:
            prefix = _prefix(r.peer_cidr or "*")
            remote = (
                f"    source_address_prefix = {q(prefix)}\n"
                if direction == "Inbound"
                else f"    destination_address_prefix = {q(prefix)}\n"
            )
            local_prefix = (
                'destination_address_prefix = "*"'
                if direction == "Inbound"
                else 'source_address_prefix = "*"'
            )
        prio = priority[r.direction]
        priority[r.direction] += 10
        if prio > 4000:
            notes.append("more than 390 rules in one direction; remaining rules omitted")
            break
        blocks.append(
            "  security_rule {\n"
            f"    name = {q(f'{r.direction}-{i:03d}')}\n"
            f"    priority = {prio}\n"
            f"    direction = {q(direction)}\n"
            '    access = "Allow"\n'
            f"    protocol = {q(_PROTO.get(r.protocol, '*'))}\n"
            '    source_port_range = "*"\n'
            f"    destination_port_range = {q(_ports(r))}\n"
            f"{remote}"
            f"    {local_prefix}\n"
            + (f"    description = {q((r.description or '')[:140])}\n" if r.description else "")
            + "  }"
        )
    if egress and not egress_is_default:
        blocks.append(
            "  security_rule {\n"
            '    name = "deny-all-outbound"\n'
            "    priority = 4096\n"
            '    direction = "Outbound"\n'
            '    access = "Deny"\n'
            '    protocol = "*"\n'
            '    source_port_range = "*"\n'
            '    destination_port_range = "*"\n'
            '    source_address_prefix = "*"\n'
            '    destination_address_prefix = "*"\n'
            '    description = "Source security group restricted egress"\n'
            "  }"
        )
    return blocks, notes


_ATTR = re.compile(r"^(\s*)([A-Za-z0-9_\"-]+)\s*=\s*(.*)$")


def fmt_hcl(text: str) -> str:
    """Align '=' across consecutive single-line attributes at the same indent (as `tofu fmt`)."""
    out: list[str] = []
    run: list[tuple[str, str, str]] = []

    def flush() -> None:
        width = max((len(k) for _, k, _ in run), default=0)
        out.extend(f"{ind}{k.ljust(width)} = {v}" for ind, k, v in run)
        run.clear()

    for line in text.split("\n"):
        m = _ATTR.match(line)
        # Opening a multi-line value (map/object) ends alignment for this run, like `tofu fmt`.
        if m and not m.group(3).endswith("{"):
            if run and run[0][0] != m.group(1):
                flush()
            run.append((m.group(1), m.group(2), m.group(3)))
            continue
        flush()
        out.append(line)
    flush()
    return "\n".join(out)


def render(
    *,
    plan_name: str,
    region: str,
    resource_group: str,
    networks: list[dict[str, Any]],
    subnets: list[dict[str, Any]],
    security_groups: list[dict[str, Any]],
    vm_bindings: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Return ({filename: content}, translation notes)."""
    notes: list[str] = []
    net_ids = {n["native_id"]: ident(n["name"], n["native_id"]) for n in networks}
    sub_ids = {
        s["native_id"]: ident(s["name"], s["native_id"])
        for s in subnets
        if s.get("network_native_id") in net_ids
    }
    sg_ids = {g["native_id"]: ident(g["name"], g["native_id"]) for g in security_groups}
    referenced = sorted(
        {
            r["peer_group_native_id"]
            for g in security_groups
            for r in g.get("rules") or []
            if r.get("peer_group_native_id") and r["peer_group_native_id"] in sg_ids
        }
    )
    asg_ids = {sg: f"asg_{sg_ids[sg]}" for sg in referenced}

    versions = (
        "terraform {\n"
        '  required_version = ">= 1.6"\n'
        "  required_providers {\n"
        f'    azurerm = {{\n      source  = "hashicorp/azurerm"\n      version = {q(AZURERM_VERSION)}\n    }}\n'
        "  }\n}\n\n"
        'provider "azurerm" {\n  features {}\n  subscription_id = var.subscription_id\n}\n'
    )
    variables = (
        'variable "subscription_id" {\n  type        = string\n  description = "Target Azure subscription"\n}\n\n'
        f'variable "location" {{\n  type    = string\n  default = {q(region)}\n}}\n\n'
        f'variable "resource_group_name" {{\n  type    = string\n  default = {q(resource_group)}\n}}\n\n'
        'variable "tags" {\n  type    = map(string)\n'
        f'  default = {{ "managed-by" = "aether-migrate", "migration-plan" = {q(azname(plan_name))} }}\n}}\n'
    )
    main = [
        "# Generated by AETHER MIGRATE. Review before applying; regenerate rather than hand-editing.\n",
        'resource "azurerm_resource_group" "migration" {\n  name     = var.resource_group_name\n'
        "  location = var.location\n  tags     = var.tags\n}\n",
    ]
    for n in networks:
        cidrs = n.get("cidrs") or []
        if not cidrs:
            notes.append(f"network {n['native_id']} has no CIDR; skipped")
            continue
        main.append(
            f'resource "azurerm_virtual_network" "{net_ids[n["native_id"]]}" {{\n'
            f"  name                = {q(azname('vnet-' + (n['name'] or n['native_id'])))}\n"
            "  location            = azurerm_resource_group.migration.location\n"
            "  resource_group_name = azurerm_resource_group.migration.name\n"
            f"  address_space       = {json.dumps(cidrs)}\n"
            "  tags                = var.tags\n}\n"
        )
    for s in subnets:
        if s["native_id"] not in sub_ids or not s.get("cidr"):
            continue
        main.append(
            f'resource "azurerm_subnet" "{sub_ids[s["native_id"]]}" {{\n'
            f"  name                 = {q(azname('snet-' + (s['name'] or s['native_id'])))}\n"
            "  resource_group_name  = azurerm_resource_group.migration.name\n"
            f"  virtual_network_name = azurerm_virtual_network.{net_ids[s['network_native_id']]}.name\n"
            f"  address_prefixes     = [{q(s['cidr'])}]\n}}\n"
        )
    for sg, asg in asg_ids.items():
        g = next(x for x in security_groups if x["native_id"] == sg)
        main.append(
            f'resource "azurerm_application_security_group" "{asg}" {{\n'
            f"  name                = {q(azname('asg-' + (g['name'] or sg)))}\n"
            "  location            = azurerm_resource_group.migration.location\n"
            "  resource_group_name = azurerm_resource_group.migration.name\n"
            "  tags                = var.tags\n}\n"
        )
    for g in security_groups:
        blocks, sg_notes = translate_rules(g, asg_ids)
        notes += [f"{g['name'] or g['native_id']}: {n}" for n in sg_notes]
        body = "\n".join(blocks)
        main.append(
            f'resource "azurerm_network_security_group" "{sg_ids[g["native_id"]]}" {{\n'
            f"  name                = {q(azname('nsg-' + (g['name'] or g['native_id'])))}\n"
            "  location            = azurerm_resource_group.migration.location\n"
            "  resource_group_name = azurerm_resource_group.migration.name\n"
            "  tags                = var.tags\n" + (body + "\n" if body else "") + "}\n"
        )

    # AWS evaluates the union of all security groups on an instance; an Azure NIC takes one
    # NSG. Machines with several groups get one merged NSG per distinct combination.
    combo_nsg: dict[tuple[str, ...], str] = {}
    for b in vm_bindings:
        combo = tuple(sorted(sg for sg in b.get("security_groups") or [] if sg in sg_ids))
        if len(combo) > 1 and combo not in combo_nsg:
            name = "nsg_merged_" + hashlib.sha1("|".join(combo).encode()).hexdigest()[:8]  # noqa: S324
            combo_nsg[combo] = name
            merged = {
                "rules": [r for g in security_groups if g["native_id"] in combo for r in g.get("rules") or []]
            }
            blocks, sg_notes = translate_rules(merged, asg_ids)
            notes += [f"merged NSG for {', '.join(combo)}: {n}" for n in sg_notes]
            main.append(
                f"# Union of {', '.join(combo)}\n"
                f'resource "azurerm_network_security_group" "{name}" {{\n'
                f"  name                = {q(azname('nsg-merged-' + name[-8:]))}\n"
                "  location            = azurerm_resource_group.migration.location\n"
                "  resource_group_name = azurerm_resource_group.migration.name\n"
                "  tags                = var.tags\n" + ("\n".join(blocks) + "\n" if blocks else "") + "}\n"
            )

    bindings = []
    for b in vm_bindings:
        subnet = sub_ids.get(b.get("subnet") or "")
        combo = tuple(sorted(sg for sg in b.get("security_groups") or [] if sg in sg_ids))
        nsgs = [combo_nsg[combo]] if combo in combo_nsg else [sg_ids[sg] for sg in combo]
        asgs = [asg_ids[sg] for sg in combo if sg in asg_ids]
        bindings.append(
            f"    {q(b['native_id'])} = {{\n"
            f"      target_size = {q(b.get('target_sku') or '')}\n"
            f"      subnet_id   = {f'azurerm_subnet.{subnet}.id' if subnet else 'null'}\n"
            f"      nsg_id      = {f'azurerm_network_security_group.{nsgs[0]}.id' if nsgs else 'null'}\n"
            f"      asg_ids     = [{', '.join(f'azurerm_application_security_group.{a}.id' for a in asgs)}]\n"
            "    }"
        )
    outputs = (
        'output "resource_group" {\n  value = azurerm_resource_group.migration.name\n}\n\n'
        'output "subnet_ids" {\n  value = {\n'
        + "".join(
            f"    {q(k)} = azurerm_subnet.{v}.id\n"
            for k, v in sub_ids.items()
            if any(s["native_id"] == k and s.get("cidr") for s in subnets)
        )
        + "  }\n}\n\n"
        "# Network bindings for each migrated machine's NIC (consumed by the replication/cutover step).\n"
        'output "machine_network_bindings" {\n  value = {\n' + "\n".join(bindings) + "\n  }\n}\n"
    )
    readme = (
        f"# Landing zone for plan “{plan_name}”\n\n"
        "Generated by AETHER MIGRATE. Apply with OpenTofu:\n\n"
        "```\ntofu init\ntofu plan -var subscription_id=<id>\ntofu apply -var subscription_id=<id>\n```\n\n"
        "Virtual machines are created by the migration tooling; `machine_network_bindings` tells it which subnet, "
        "NSG and ASGs each machine's NIC uses.\n"
        + ("\n## Translation notes\n\n" + "\n".join(f"- {n}" for n in notes) + "\n" if notes else "")
    )
    files = {
        "versions.tf": versions,
        "variables.tf": variables,
        "main.tf": "\n".join(main),
        "outputs.tf": outputs,
    }
    return {**{k: fmt_hcl(v) for k, v in files.items()}, "README.md": readme}, notes
