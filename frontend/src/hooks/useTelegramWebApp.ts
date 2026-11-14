import { useEffect, useState, useCallback } from 'react';

interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

interface TelegramSafeArea {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

interface TelegramWebAppInstance {
  initData: string;
  initDataUnsafe: {
    user?: TelegramUser;
    auth_date?: number;
    hash?: string;
  };
  version: string;
  platform: string;
  colorScheme: 'light' | 'dark';
  isExpanded: boolean;
  viewportHeight: number;
  viewportStableHeight: number;
  safeAreaInset?: TelegramSafeArea;
  contentSafeAreaInset?: TelegramSafeArea;
  ready: () => void;
  expand: () => void;
  close: () => void;
  setHeaderColor: (color: string) => void;
  setBackgroundColor: (color: string) => void;
  enableClosingConfirmation?: () => void;
  HapticFeedback?: {
    impactOccurred: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft') => void;
    notificationOccurred: (type: 'error' | 'success' | 'warning') => void;
    selectionChanged: () => void;
  };
  onEvent?: (eventType: string, callback: () => void) => void;
  offEvent?: (eventType: string, callback: () => void) => void;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebAppInstance;
    };
  }
}

export interface TelegramContextState {
  isTelegramEnv: boolean;
  initData: string;
  user: TelegramUser | null;
  platform: string;
  viewportHeight: number;
  safeTop: number;
  safeBottom: number;
  hapticSelection: () => void;
  hapticImpact: (style?: 'light' | 'medium' | 'heavy') => void;
}

export function useTelegramWebApp(): TelegramContextState {
  const [state, setState] = useState<Omit<TelegramContextState, 'hapticSelection' | 'hapticImpact'>>({
    isTelegramEnv: false,
    initData: '',
    user: null,
    platform: 'web',
    viewportHeight: typeof window !== 'undefined' ? window.innerHeight : 900,
    safeTop: 0,
    safeBottom: 0
  });

  useEffect(() => {
    const tg = window.Telegram?.WebApp;
    if (!tg) return;

    const isTg = Boolean(tg.initData || tg.platform !== 'unknown');

    try {
      tg.ready();
      tg.expand();
      tg.setHeaderColor?.('#080305');
      tg.setBackgroundColor?.('#080305');
    } catch {
      // Safe fallback outside Telegram client
    }

    const syncViewport = () => {
      const topInset = (tg.safeAreaInset?.top || 0) + (tg.contentSafeAreaInset?.top || 0);
      const bottomInset = (tg.safeAreaInset?.bottom || 0) + (tg.contentSafeAreaInset?.bottom || 0);

      document.documentElement.style.setProperty('--tg-viewport-safe-top', `${topInset}px`);
      document.documentElement.style.setProperty('--tg-viewport-safe-bottom', `${bottomInset}px`);

      setState({
        isTelegramEnv: isTg,
        initData: tg.initData || '',
        user: tg.initDataUnsafe?.user || null,
        platform: tg.platform || 'web',
        viewportHeight: tg.viewportStableHeight || window.innerHeight,
        safeTop: topInset,
        safeBottom: bottomInset
      });
    };

    syncViewport();
    tg.onEvent?.('viewportChanged', syncViewport);
    tg.onEvent?.('safeAreaChanged', syncViewport);
    tg.onEvent?.('contentSafeAreaChanged', syncViewport);

    return () => {
      tg.offEvent?.('viewportChanged', syncViewport);
      tg.offEvent?.('safeAreaChanged', syncViewport);
      tg.offEvent?.('contentSafeAreaChanged', syncViewport);
    };
  }, []);

  const hapticSelection = useCallback(() => {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.selectionChanged();
    } catch {
      // Ignore outside Telegram
    }
  }, []);

  const hapticImpact = useCallback((style: 'light' | 'medium' | 'heavy' = 'light') => {
    try {
      window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(style);
    } catch {
      // Ignore outside Telegram
    }
  }, []);

  return {
    ...state,
    hapticSelection,
    hapticImpact
  };
}
