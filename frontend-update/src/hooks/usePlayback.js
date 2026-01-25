/**
 * usePlayback - Hook for managing event playback state
 * 
 * Handles:
 * - Play/pause state with auto-advance interval
 * - Seeking and skipping through events
 * - "Follow live" mode that auto-advances to latest events
 * - Properly separated from connection state
 */

import { useState, useEffect, useCallback, useRef } from "react";

/**
 * @param {Object} options
 * @param {number} options.totalEvents - Total number of events available
 * @param {boolean} options.isLive - Whether the stream is currently live (connected + not ended)
 * @param {number} options.playbackSpeed - Milliseconds between events (default: 1000)
 * @returns {Object} Playback state and controls
 */
export function usePlayback({ totalEvents = 0, isLive = false, playbackSpeed = 1000 }) {
  // Core playback state
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  
  // Follow live mode - when true, auto-advance to latest events as they arrive
  const [followLive, setFollowLive] = useState(true);
  
  // Ref to track latest totalEvents for the interval callback
  const totalEventsRef = useRef(totalEvents);
  useEffect(() => {
    totalEventsRef.current = totalEvents;
  }, [totalEvents]);
  
  // Interval ref for cleanup
  const intervalRef = useRef(null);

  // Clear any existing interval
  const clearPlaybackInterval = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  // Auto-advance when playing
  useEffect(() => {
    clearPlaybackInterval();
    
    if (!isPlaying) return;
    
    intervalRef.current = setInterval(() => {
      setCurrentIndex((prev) => {
        const maxIndex = totalEventsRef.current - 1;
        if (maxIndex < 0) return 0;
        if (prev >= maxIndex) {
          // Reached the end - stop playing
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, playbackSpeed);

    return clearPlaybackInterval;
  }, [isPlaying, playbackSpeed, clearPlaybackInterval]);

  // Follow live - auto-advance to latest when new events arrive
  useEffect(() => {
    if (followLive && isLive && totalEvents > 0) {
      setCurrentIndex(totalEvents - 1);
    }
  }, [followLive, isLive, totalEvents]);

  // Play/Pause toggle
  const togglePlay = useCallback(() => {
    setIsPlaying((prev) => !prev);
  }, []);

  // Play
  const play = useCallback(() => {
    setIsPlaying(true);
  }, []);

  // Pause
  const pause = useCallback(() => {
    setIsPlaying(false);
  }, []);

  // Seek to specific index
  const seek = useCallback((index) => {
    const maxIndex = Math.max(0, totalEvents - 1);
    const clampedIndex = Math.max(0, Math.min(index, maxIndex));
    setCurrentIndex(clampedIndex);
    // When user seeks, disable follow live
    setFollowLive(false);
  }, [totalEvents]);

  // Skip to previous event
  const skipPrev = useCallback(() => {
    setCurrentIndex((prev) => Math.max(0, prev - 1));
    // When user skips, disable follow live
    setFollowLive(false);
  }, []);

  // Skip to next event
  const skipNext = useCallback(() => {
    setCurrentIndex((prev) => Math.min(totalEvents - 1, prev + 1));
    // When user skips, disable follow live  
    setFollowLive(false);
  }, [totalEvents]);

  // Go to live (latest event)
  const goToLive = useCallback(() => {
    if (totalEvents > 0) {
      setCurrentIndex(totalEvents - 1);
    }
    setFollowLive(true);
  }, [totalEvents]);

  // Check if at the latest event
  const isAtLatest = currentIndex >= totalEvents - 1;

  return {
    // State
    isPlaying,
    currentIndex,
    followLive,
    isAtLatest,
    
    // Controls
    togglePlay,
    play,
    pause,
    seek,
    skipPrev,
    skipNext,
    goToLive,
    setFollowLive,
  };
}

export default usePlayback;
