import { useEffect, useRef } from 'react'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

// Modal keyboard behaviour: focus moves into the dialog (to `initial` or its first control), Tab and
// Shift+Tab cycle inside it, Escape closes, and focus returns to whatever opened it.
export function useDialog(close, initial) {
  const ref = useRef(null)
  const closeRef = useRef(close)
  closeRef.current = close

  useEffect(() => {
    const opener = document.activeElement
    const box = ref.current
    const first = initial?.current || box?.querySelector(FOCUSABLE)
    first?.focus()
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        closeRef.current()
        return
      }
      if (e.key !== 'Tab' || !box) return
      const items = [...box.querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null)
      if (!items.length) return
      const [a, z] = [items[0], items[items.length - 1]]
      if (e.shiftKey && (document.activeElement === a || !box.contains(document.activeElement))) {
        e.preventDefault()
        z.focus()
      } else if (!e.shiftKey && (document.activeElement === z || !box.contains(document.activeElement))) {
        e.preventDefault()
        a.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus()
    }
  }, [initial])

  return ref
}
