# Security Policy

## Supported versions

Only the `main` branch is supported. The deployed Azure infrastructure is managed from `main`; older commits and feature branches are not maintained.

## Reporting a vulnerability

Please report security issues **privately** through GitHub Security Advisories:

1. Open the repository **Security** tab and choose **Report a vulnerability**.
2. Do not open a public issue or pull request with exploit details.
3. Include the affected file or workflow, impact, reproduction steps, and a suggested fix if you have one.

Do not include live credentials, subscription or tenant IDs, Terraform state, plan files, or decrypted artifacts in a report. If a secret may have been exposed, rotate it first and tell us only the type and location.

We aim to acknowledge reports within 3 business days and to share a remediation plan within 10 business days. Please allow time for a fix before public disclosure.

## Scope

In scope:

- GitHub Actions workflows, composite scripts, and repository settings in this repository.
- Terraform modules and roots (`tf/`, `tf-test/`, `tf-foundation/`) and their security controls.
- The plan encryption, approval, and artifact handling implemented here.

Out of scope:

- The Azure subscription itself and its data-plane resources. Report those to the subscription owner or the Atea security team.
- Third-party actions and providers; report upstream and open an issue here if the pinned version is affected.

## Contributor expectations

- Never commit Terraform state (`*.tfstate`), plans (`*.tfplan`), private keys, or local tfvars.
- Keep GitHub Actions pinned to full commit SHAs; the repository requires SHA pinning.
- Keep subscription-scoped resources in `tf-foundation`, not in the workload roots.
- Do not weaken production approval, plan encryption, or the read-only/apply identity split.

## Commit signing

Signed commits are recommended for `main`. Enforcement is intentionally not enabled by the repository ruleset yet, so contributors without signing configured are not blocked; this may change once every maintainer has signing set up.
