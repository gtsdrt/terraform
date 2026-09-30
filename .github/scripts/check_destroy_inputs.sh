#!/usr/bin/env bash
set -euo pipefail
[[ "${DESTROY_CONFIRM:-}" == DESTROY ]] || { echo 'Confirmation must be exactly DESTROY' >&2; exit 1; }
case "${DESTROY_ENV:-}" in tf|tf-test) ;; *) echo 'Invalid environment' >&2; exit 1 ;; esac
case "${DESTROY_SCOPE:-}" in all|azlandingzone|network) ;; *) echo 'Invalid scope' >&2; exit 1 ;; esac
if [[ "$DESTROY_ENV" == tf-test && "$DESTROY_SCOPE" != all ]]; then
  echo 'tf-test only supports scope=all' >&2
  exit 1
fi
