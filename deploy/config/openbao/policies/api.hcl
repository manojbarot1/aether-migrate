# API service policy
# The API can list and read credential metadata but CANNOT read cloud secrets.
# Secrets are fetched only by the connector worker at discovery/execution time.

path "cloud-creds/metadata/*" {
  capabilities = ["list", "read"]
}

# API can encrypt (for storing new credentials) but NOT decrypt
path "transit/encrypt/*" {
  capabilities = ["update"]
}

# Allow API to renew its own token
path "auth/token/renew-self" {
  capabilities = ["update"]
}
