import os
import uuid

identities = [os.environ.get(name, "") for name in
              ("PLAN_CLIENT_ID", "TEST_CLIENT_ID", "PROD_CLIENT_ID")]
try:
    parsed = [uuid.UUID(identity) for identity in identities]
    if len(set(parsed)) != 3:
        raise ValueError("Identity reuse")
except ValueError:
    raise SystemExit("Configure three distinct plan/test/prod Azure client IDs before deploying")
