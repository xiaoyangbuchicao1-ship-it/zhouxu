"use client";

import { useEffect } from "react";

/** Keyboard paths stay immediate; pointer feedback remains a progressive enhancement. */
export function InputModality() {
  useEffect(() => {
    const keyboard = () => { document.documentElement.dataset.input = "keyboard"; };
    const pointer = () => { document.documentElement.dataset.input = "pointer"; };
    window.addEventListener("keydown", keyboard, true);
    window.addEventListener("pointerdown", pointer, true);
    return () => { window.removeEventListener("keydown", keyboard, true); window.removeEventListener("pointerdown", pointer, true); };
  }, []);
  return null;
}
