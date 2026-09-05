"use client";

import { useCallback, useEffect, useRef } from "react";

import { useCaseSession } from "./case-session-provider";

/** Capture ownership for page-local results, including navigation after a write. */
export function useSessionUiOperation() {
  const { getSessionEpoch } = useCaseSession();
  const lifetimeRef = useRef(0);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; lifetimeRef.current += 1; };
  }, []);

  return useCallback(() => {
    const lifetime = lifetimeRef.current;
    const epoch = getSessionEpoch();
    return () => mountedRef.current && lifetimeRef.current === lifetime && getSessionEpoch() === epoch;
  }, [getSessionEpoch]);
}
