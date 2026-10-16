import { useEffect, useState } from 'react';
import { fetchPublishingPolicy } from '../lib/api';
import type { PublishingPolicy } from '../lib/api';
import AutomatedPublishPage from './PublishPage';
import ManualPublishPage from './ManualPublishPage';

export default function PublishingPage({ persona }: { persona: string }) {
  const [policy, setPolicy] = useState<PublishingPolicy | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    setError('');
    fetchPublishingPolicy().then((value) => { if (active) setPolicy(value); })
      .catch(() => { if (active) setError('无法读取发布方式，请重试。'); });
    return () => { active = false; };
  }, [retry]);
  if (error) return <div className="panel" role="alert">{error}
    <button className="btn btn-sm" onClick={() => setRetry((value) => value + 1)}>重新读取</button>
  </div>;
  if (!policy) return <div className="dash-empty">正在读取发布方式…</div>;
  return policy.mode === 'automated' && policy.automaticSubmission
    ? <AutomatedPublishPage persona={persona} /> : <ManualPublishPage persona={persona} />;
}
