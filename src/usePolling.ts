import {useEffect,useRef} from 'react';

/**
 * Interval that only runs while the tab is visible and only while `active`.
 *
 * The results stage otherwise stacks several independent timers on top of two SSE
 * streams, all of which keep hitting the API from a tab nobody is looking at. The
 * callback is read from a ref so callers can pass a fresh closure each render without
 * restarting the timer.
 */
export function usePolling(callback:()=>unknown,intervalMs:number,active=true){
  const saved=useRef(callback);
  useEffect(()=>{saved.current=callback;});
  useEffect(()=>{
    if(!active) return;
    let timer:ReturnType<typeof setInterval>|undefined;
    let stopped=false;
    const tick=()=>{if(!document.hidden) Promise.resolve(saved.current()).catch(()=>{});};
    const start=()=>{
      if(timer!==undefined||stopped) return;
      tick();
      timer=setInterval(tick,intervalMs);
    };
    const stop=()=>{if(timer!==undefined){clearInterval(timer);timer=undefined;}};
    const onVisibility=()=>{if(document.hidden) stop(); else start();};
    start();
    document.addEventListener('visibilitychange',onVisibility);
    return()=>{stopped=true;stop();document.removeEventListener('visibilitychange',onVisibility);};
  },[intervalMs,active]);
}
