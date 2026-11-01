# Reporting deploys

Deploys shortly before an incident are among the best clues to its cause. When a pipeline reports its deploys, they show up on the incident's timeline and as evidence (`ev_deploy_1`, ...) for incidents on the same service.

```
POST /v1/deploys
Authorization: Bearer <IIP_WEBHOOK_TOKEN>
Content-Type: application/json

{
  "service": "checkout",          required, the same name your alerts use
  "version": "v2.4.1",            optional
  "commit_sha": "3f2a9c1",        optional
  "deployed_at": "2026-11-01T14:02:00Z",   optional, defaults to now
  "description": "Retry payment calls",    optional
  "metadata": {"pipeline": "ci-1234"}       optional
}
```

The response is `201` with the deploy's `id` and `incidents_to_recheck`: open incidents on that service, whose evidence is collected again a minute later (a rollback or fix during an incident is new evidence).

## From GitHub Actions

Add a step after your deploy step. Store the token as a repository secret.

```yaml
- name: Report the deploy
  if: success()
  run: |
    curl -fsS -X POST "$IIP_URL/v1/deploys" \
      -H "Authorization: Bearer $IIP_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"service\": \"checkout\", \"version\": \"${GITHUB_REF_NAME}\", \"commit_sha\": \"${GITHUB_SHA}\"}"
  env:
    IIP_URL: https://your-iip.example.com
    IIP_TOKEN: ${{ secrets.IIP_WEBHOOK_TOKEN }}
```
