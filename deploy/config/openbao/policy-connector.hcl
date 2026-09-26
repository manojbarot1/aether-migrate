# aether-connector: read-only access to connection and platform secrets.
path "cloud-creds/data/ws/*" {
  capabilities = ["read"]
}
path "cloud-creds/data/platform/*" {
  capabilities = ["read"]
}
