import {useCallback, useEffect, useState, useSyncExternalStore} from 'react';
import {createSnapshotClient} from './snapshot-client.js';

export default function useSnapshot() {
  const [client] = useState(() => {
    let storage;
    try {storage = window.localStorage;} catch { /* Storage is optional. */ }
    return createSnapshotClient({storage});
  });
  const state = useSyncExternalStore(client.subscribe, client.getState);
  const [live, setLive] = useState(false);
  const refresh = useCallback(() => client.refresh(), [client]);
  useEffect(() => {
    refresh();
    const stream = new EventSource('/api/events');
    stream.onopen = () => setLive(true);
    stream.onmessage = event => {
      setLive(true);
      if (event.data !== client.getState().data?.revision) refresh();
    };
    stream.onerror = () => setLive(false);
    const timer = setInterval(refresh, 30000);
    return () => {stream.close(); clearInterval(timer); client.stop();};
  }, [client, refresh]);
  return {...state, live, refresh};
}
