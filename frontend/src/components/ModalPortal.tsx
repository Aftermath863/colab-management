import React from 'react';
import { createPortal } from 'react-dom';

/**
 * Shared backdrop classes for modal dialogs:
 * - Fixed full-viewport overlay on `document.body` (when mounted via `ModalPortal`)
 * - Vertical scrolling (`overflow-y-auto`) with `items-start` + `my-auto` on `.mac-modal-panel`
 *   so short modals center vertically while tall forms never clip above the macOS titlebar.
 */
export const MODAL_BACKDROP_CLASSES =
  'mac-modal-backdrop fixed inset-0 z-[80] overflow-y-auto flex items-start justify-center px-4 pt-12 pb-8 sm:pt-14 sm:pb-10';

/**
 * SSR-safe portal that mounts modals directly under `document.body` in the browser / WKWebView,
 * isolating them from any ancestor `transform`, `backdrop-filter`, or overflow clipping, while
 * falling back to inline rendering during Node `renderToStaticMarkup` tests.
 */
export const ModalPortal: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  if (
    typeof window !== 'undefined' &&
    typeof document !== 'undefined' &&
    document.body &&
    typeof document.createElement === 'function'
  ) {
    return createPortal(children, document.body);
  }
  return <>{children}</>;
};
