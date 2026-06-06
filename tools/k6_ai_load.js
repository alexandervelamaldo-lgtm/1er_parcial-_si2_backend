import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS, 10) : 50,
  duration: __ENV.K6_DURATION || '20s',
  thresholds: {
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(95)<2500'],
  },
};

const baseUrl = (__ENV.K6_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const token = __ENV.K6_TOKEN || '';

export default function () {
  const headers = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };

  const payload = JSON.stringify({
    action: 'resumir',
    text: 'Tengo una llanta pinchada y el auto no avanza. Estoy en Santa Cruz, por el tercer anillo.',
    tone: 'profesional',
    length: 'corto',
  });

  const res = http.post(`${baseUrl}/ai/text/generate`, payload, { headers });
  check(res, {
    'status is 2xx': (r) => r.status >= 200 && r.status < 300,
  });
  sleep(0.2);
}

