# aether-backup: take Raft snapshots (encrypted by OpenBao's barrier). Nothing else.
path "sys/storage/raft/snapshot" {
  capabilities = ["read"]
}
