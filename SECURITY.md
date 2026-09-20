# Security Posture

## Scan results (cfn-guard, aws-security rule set)

Applied fixes:
- **S3 ACLs disabled** (`ObjectOwnership: BucketOwnerEnforced`) — no ACL-based
  access possible, on top of full `PublicAccessBlockConfiguration`.
- **`bedrock:InvokeModel` narrowed** from `*` to foundation-model and
  inference-profile ARN classes (both the gateway role and the router Lambda).
  Model choice is operator-configurable at runtime, so per-model ARNs are not
  static; the ARN-class scope is the tightest expressible policy.

Accepted findings (documented, not applied — rationale):
- **S3 Object Lock**: the config bucket holds one mutable, hot-reloaded file;
  WORM semantics would break the solution's core admin workflow. Versioning is
  enabled for rollback.
- **S3 access logging / replication**: single-file internal config bucket, no
  data-plane traffic. Enable per your organization's baseline if required.
- **IAM inline policies**: roles are single-purpose and stack-scoped; inline
  policies tie their lifecycle to the role and prevent out-of-band reuse.
- **`bedrock-agentcore:*` on the provisioner role**: gateway creation has
  hidden service dependencies (workload identity provisioning). This role is
  attached only to the CloudFormation custom-resource Lambda, which is never
  invocable by request traffic.

## Data flow notes

- Gateway auth is AWS IAM (SigV4); there are no public or anonymous endpoints.
- With `DecisionEngine=jev`, prompt text (first 32k chars) transits the
  configured external decision API over TLS. Use `DecisionEngine=bedrock` to
  keep all data in-account.
- The Jev API key is stored in Secrets Manager, passed at deploy time via a
  NoEcho parameter, and read by the router Lambda at runtime only.

## Reporting

Open a GitHub issue (do not include secrets or account identifiers).
