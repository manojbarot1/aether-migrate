# OpenBao server configuration (single node, integrated Raft storage).
# TLS: the listener is reachable only on the internal `secrets` Docker network.
# Enable TLS here if the network boundary is not sufficient for your threat model.
ui            = false
api_addr      = "http://openbao:8200"
cluster_addr  = "http://openbao:8201"

storage "raft" {
  path    = "/openbao/file"
  node_id = "openbao-1"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
  # Break-glass root-token generation (`aetherctl bao-reconfigure`, restore) still
  # requires a quorum of unseal-key holders; this listener is reachable only from the
  # internal `secrets` network. OpenBao 2.x disables these endpoints by default.
  disable_unauthed_generate_root_endpoints = false
}

log_format = "json"

# Every request to OpenBao (secret reads by the connector included) is audited.
# Sensitive values are HMAC'd, not written in clear.
audit "file" "file" {
  description = "Request/response audit trail"
  options {
    file_path = "/openbao/logs/audit.log"
    mode      = "0600"
  }
}
