# Scalable Serverless Scanning Pipeline

An asynchronous accessibility-scanning pipeline on AWS, built to stay inside the Free Tier. Clients submit up to 10 URLs to a secured API. Each URL is queued, scanned by a rate-limited Lambda worker, and stored in DynamoDB and S3. Failed scans land in a dead-letter queue and trigger an email alert.

This is Project 2 in a series. Project 1 is [accessibility-scanner](https://github.com/Canzo32/accessibility-scanner), a simple scheduled scanner.

> **Note:** `lambda/submit_scans.py` was reconstructed from my design notes after I deleted the original Lambda to avoid AWS charges. `lambda/scan_worker.py` is the exact code that was deployed and tested.

## Problem

A synchronous scanning API hangs or fails when a single page times out or a URL is bad. I wanted instant submission, isolated failures, and failures that are visible rather than silent.

## Architecture

![Architecture](architecture/architecture.png)

1. The client sends `POST /scan` with an `x-api-token` header and 1 to 10 URLs.
2. **SubmitScans** checks the token, filters unsafe URLs (SSRF protection), sends one SQS message per URL, and returns `202` with a `batchId`.
3. **ScanWorker** reads batches of up to 5 messages, with at most 2 running at once. It fetches each page, runs the checks, then writes a JSON report to S3 and a summary item to DynamoDB.
4. A message that fails 3 times moves to **ScanDLQ**.
5. A **CloudWatch alarm** on the DLQ notifies an **SNS** email topic.

## Design Decisions

| Choice | Reason |
|---|---|
| SQS between API and scanner | API responds instantly, scans run asynchronously |
| Lambda workers, not Fargate | Scales to zero, free when idle |
| No VPC | A Lambda in a VPC needs a NAT Gateway (about $32/month) to reach the internet |
| Max concurrency 2 and API throttling (rate 2, burst 5) | Protects the budget and the sites being scanned |
| DLQ after 3 receives, visibility timeout 180s | Bad URLs stop retrying forever |
| Partial batch failures (`batchItemFailures`) | One bad URL does not force the others to be re-scanned |
| SSRF protection in SubmitScans | Rejects non-http(s) URLs and private, loopback and link-local addresses |
| Shared-secret header | Simple auth (Cognito or IAM in production) |
| Scoped inline IAM policy | Least privilege, no `FullAccess` policies |

## What It Checks

Images missing `alt` text, empty links, missing `<title>`, and a missing `lang` attribute. This is a lightweight HTML check, not a full WCAG audit.

## Example

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://<API_ID>.execute-api.us-east-1.amazonaws.com/scan" `
  -Headers @{"x-api-token"="<YOUR_API_TOKEN>"} `
  -ContentType "application/json" `
  -Body '{"urls":["https://example.com","https://www.w3.org"]}'
```

```json
{ "batchId": "be37463a-c78b-4e8b-8e8c-696087e9e8e7", "queued": 2, "rejected": {} }
```

A URL such as `http://localhost` is rejected by the SSRF check and listed under `rejected`.

**Results:** `https://example.com` returned 0 issues. `https://www.w3.org` returned 5 issues (images missing alt text).

## Screenshots

| | |
|---|---|
| **DynamoDB scan results** | **S3 JSON reports** |
| ![DynamoDB](screenshots/dynamodb-results.png) | ![S3](screenshots/s3-reports.png) |
| **SQS queues** | **Dead-letter queue message** |
| ![SQS](screenshots/sqs-queues.png) |  |
| **CloudWatch alarm** | **CloudWatch dashboard** |
| ![Alarm](screenshots/cloudwatch-alarm.png) | ![Dashboard](screenshots/cloudwatch-dashboard.png) |
| **API Gateway routes** | **IAM policy** |
| ![API](screenshots/api-gateway-routes.png) | ![IAM](screenshots/iam-policy.png) |

## Lessons Learned

- **"Errors: 0" does not mean success.** The worker catches its own exceptions and returns `batchItemFailures`, so Lambda reported 100% success while every scan was failing. The real error was only visible in CloudWatch logs.
- **An S3 IAM resource needs `/*`.** The policy covered the bucket but not the objects in it, which caused `AccessDenied` on `PutObject`.
- **"Report batch item failures" must be enabled on the SQS trigger.** It was off, which means failed messages are deleted instead of retried, so they never reach the DLQ.
- **Keep every resource in one region.** I initially created some resources in the wrong region and had to rebuild them.

## Known Limitations and What I'd Improve

- **Auth:** replace the shared secret with Cognito or IAM authorization.
- **SSRF hardening:** the worker follows redirects, and DNS is resolved at submit time and again at fetch time, so a hostile site could redirect to a private address. A production version would re-validate at fetch time and disable redirects.
- **Scan depth:** use axe-core or Pa11y for real WCAG 2.1 AA coverage.
- **Infrastructure as code:** a CloudFormation or Terraform template for one-click deploy.
- **Status endpoint:** a `GET /scan/{batchId}` route to check progress.

## Cost

Built for the AWS Free Tier. A zero-spend budget alert was active throughout, and all resources were deleted after testing. There are no always-on components (no VPC, NAT Gateway, Fargate or EC2), so idle cost is $0.

## Repository Structure

```
lambda/    scan_worker.py (deployed), submit_scans.py (reconstructed)
iam/       scan-pipeline-access-policy.json
architecture/   architecture.png
screenshots/    8 console screenshots
```

Account ID and bucket name in the IAM policy are placeholders.
