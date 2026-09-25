# Connector worker policy
# The connector worker can read cloud credentials and use transit for encryption/decryption.

path "cloud-creds/*" {
  capabilities = ["read"]
}

path "transit/encrypt/*" {
  capabilities = ["update"]
}

path "transit/decrypt/*" {
  capabilities = ["update"]
}

# Allow connector worker to renew its own token
path "auth/token/renew-self" {
  capabilities = ["update"]
}
