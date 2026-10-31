import boto3, json, os, re, urllib.request
from datetime import datetime, timezone

s3 = boto3.client('s3')
table = boto3.resource('dynamodb').Table(os.environ['DYNAMODB_TABLE'])
BUCKET = os.environ['S3_BUCKET']

def check_accessibility(html):
    issues = []
    for img in re.findall(r'<img[^>]*>', html, re.IGNORECASE):
        if 'alt=' not in img.lower():
            issues.append(f"Image missing alt text: {img[:80]}")
    for link in re.findall(r'<a[^>]*>\s*</a>', html, re.IGNORECASE):
        issues.append(f"Empty link: {link[:80]}")
    if '<title>' not in html.lower():
        issues.append("Missing <title> tag")
    if '<html' in html.lower() and 'lang=' not in html.lower():
        issues.append("Missing lang attribute on <html>")
    return issues

def lambda_handler(event, context):
    failures = []
    for record in event['Records']:
        try:
            msg = json.loads(record['body'])
            url = msg['url']
            req = urllib.request.Request(url, headers={'User-Agent': 'AccessibilityScanner/2.0'})
            with urllib.request.urlopen(req, timeout=10) as r:
                html = r.read(2_000_000).decode('utf-8', errors='ignore')
            issues = check_accessibility(html)
            scan_time = datetime.now(timezone.utc).isoformat()
            report = {'url': url, 'scanTime': scan_time, 'batchId': msg.get('batchId', ''),
                      'issueCount': len(issues), 'issues': issues}
            safe_name = re.sub(r'[^a-zA-Z0-9]', '_', url)[:100]
            s3.put_object(Bucket=BUCKET, Key=f"pipeline-reports/{scan_time[:10]}/{safe_name}.json",
                          Body=json.dumps(report, indent=2), ContentType='application/json')
            table.put_item(Item=report)
            print(f"Scanned {url}: {len(issues)} issues")
        except Exception as e:
            print(f"FAILED {record['messageId']}: {e}")
            failures.append({'itemIdentifier': record['messageId']})
    return {'batchItemFailures': failures}
