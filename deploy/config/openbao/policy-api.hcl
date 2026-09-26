# aether-api: may create, rotate and destroy connection secrets. It can never read them.
path "cloud-creds/data/ws/*" {
  capabilities = ["create", "update"]
}
path "cloud-creds/metadata/ws/*" {
  capabilities = ["delete"]
}
# Platform identity (e.g. the AWS principal customers trust), managed by platform admins via the API.
path "cloud-creds/data/platform/*" {
  capabilities = ["create", "update"]
}
path "cloud-creds/metadata/platform/*" {
  capabilities = ["delete"]
}
