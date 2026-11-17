"""Actual Google Places Text Search (New); never substitutes another provider."""
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ENDPOINT = 'https://places.googleapis.com/v1/places:searchText'


def configured():
    return bool(os.environ.get('GOOGLE_PLACES_API_KEY'))


def search_page(query, page_token=''):
    from local_leads import CollectionError
    key = os.environ.get('GOOGLE_PLACES_API_KEY', '')
    if not key:
        raise CollectionError('Google Maps 尚未配置 GOOGLE_PLACES_API_KEY；未请求 Google，也未回退为 Bing。')
    body = {'textQuery': query, 'pageSize': 20, 'languageCode': 'en', 'regionCode': 'AU'}
    if page_token:
        body['pageToken'] = page_token
    request = Request(ENDPOINT, data=json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'X-Goog-Api-Key': key,
        'X-Goog-FieldMask': 'places.id,places.displayName,places.formattedAddress,places.websiteUri,places.nationalPhoneNumber,places.googleMapsUri,nextPageToken'})
    try:
        with urlopen(request, timeout=25) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise CollectionError(f'Google Places HTTP {exc.code}；请检查 API 启用、权限及额度；未回退其他来源。') from None
    if not isinstance(data, dict) or 'error' in data:
        raise CollectionError('Google Places 返回错误或格式不正确')
    items = []
    for place in data.get('places', []):
        address = re.sub(r',?\s*Australia\s*$', '', place.get('formattedAddress', ''))
        items.append({'id': place['id'], 'title': place.get('displayName', {}).get('text', ''),
                      'address': address, 'website': place.get('websiteUri', ''),
                      'phone': place.get('nationalPhoneNumber', ''), 'source': place.get('googleMapsUri', '')})
    return items, data.get('nextPageToken', '')
