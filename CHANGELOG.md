# Changelog

## v1.0.0 (2026-09-19)
- Initial release: AgentCore Gateway inference routing with REQUEST-interceptor
  decision engine (TypeSafe Jev or in-account Bedrock classifier).
- 19-class task taxonomy, S3 hot-reload admin config, DynamoDB decision log,
  CloudWatch metrics, fail-up design.
- CloudFormation packaging with custom-resource gateway provisioner
  (idempotent create, self-cleaning on partial failure, retained-delete safe).
- Benchmark: 585 labeled prompts, 4-engine comparison (eval/RESULTS.md).
