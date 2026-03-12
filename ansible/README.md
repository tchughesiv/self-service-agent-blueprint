# Ansible Demo Deployment

Run from **repo root** only — playbooks resolve paths relative to it.

- `make ansible-apply-demo NAMESPACE=<ns>` — export then apply
- `make ansible-teardown-demo NAMESPACE=<ns>` — teardown
- Export manually: `make helm-export-demo`, then `ansible-playbook -i localhost, ansible/playbooks/apply-demo-manifests.yml -e "target_namespace=<ns>"` (from root)

See [docs/HELM_EXPORT_ANSIBLE.md](../docs/HELM_EXPORT_ANSIBLE.md) for env vars and details.
