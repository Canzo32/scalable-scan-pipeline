"""
SubmitScans - API entry point for the scanning pipeline.

NOTE: Reconstructed from the project's design notes after the original
Lambda was deleted to avoid AWS charges. It implements the same behavior
(token auth, 1-10 URLs, SSRF checks, batched SQS enqueue, 202 response)
but is not a byte-for-byte copy of the deployed version.

Environment variables: QUEUE_URL, API_TOKEN
"""
import boto3, hmac, ipaddress, json, os, socket, uuid
from urllib.parse import urlparse

sqs = boto3.client('sqs')
QUEUE_URL = os.environ['QUEUE_URL']
API_TOKEN = os.environ['API_TOKEN']

MAX_URLS = 10


def response(status, body):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }


def is_safe(url):
    """SSRF protection: allow only http(s) URLs that resolve to public IPs."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return False
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False


def lambda_handler(event, context):
    # 1. Auth: shared-secret header (HTTP API lowercases header names)
    token = (event.get('headers') or {}).get('x-api-token', '')
    if not hmac.compare_digest(token, API_TOKEN):
        return response(401, {'error': 'Unauthorized'})

    # 2. Parse and validate the body
    try:
        body = json.loads(event.get('body') or '{}')
    except json.JSONDecodeError:
        return response(400, {'error': 'Body must be valid JSON'})

    urls = body.get('urls')
    if not isinstance(urls, list) or not 1 <= len(urls) <= MAX_URLS:
        return response(400, {'error': f'Provide 1 to {MAX_URLS} URLs in "urls"'})

    # 3. SSRF filtering
    accepted, rejected = [], {}
    for url in urls:
        if isinstance(url, str) and is_safe(url):
            accepted.append(url)
        else:
            rejected[str(url)] = 'rejected: not a public http(s) URL'

    batch_id = str(uuid.uuid4())

    # 4. Enqueue one message per URL
    queued = 0
    if accepted:
        entries = [
            {
                'Id': str(i),
                'MessageBody': json.dumps({'url': url, 'batchId': batch_id}),
            }
            for i, url in enumerate(accepted)
        ]
        result = sqs.send_message_batch(QueueUrl=QUEUE_URL, Entries=entries)
        queued = len(result.get('Successful', []))
        for failed in result.get('Failed', []):
            rejected[accepted[int(failed['Id'])]] = 'failed to enqueue'

    return response(202, {'batchId': batch_id, 'queued': queued, 'rejected': rejected})
