// Чистые функции всплывающего Markdown-меню (без React) — используются
// компонентом MdTextarea. Синтаксис ровно тот, что понимает бэкенд
// (backend/app/integrations/_mdfmt.py): **жирный**, *курсив*,
// __подчёркнутый__, ~~зачёркнутый~~, `код`, [текст](url), «# заголовок».

export interface MdResult {
  value: string
  start: number
  end: number
}

/**
 * Обернуть выделение маркерами или снять уже стоящие (повторный клик).
 * При пустом выделении вставляется placeholder и выделяется для ввода.
 */
export function mdWrap(
  value: string,
  start: number,
  end: number,
  before: string,
  after: string,
  placeholder = 'текст',
): MdResult {
  const sel = value.slice(start, end)
  const pre = value.slice(0, start)
  const post = value.slice(end)

  // Вокруг выделения уже стоят маркеры — снимаем их (повторный клик).
  if (sel && pre.endsWith(before) && post.startsWith(after)) {
    const dbl = before + before
    // Не путаем одиночную «*» с частью «**» (и «_» с «__»): если выделение
    // внутри **жирного**, снимаем двойные маркеры целиком.
    if (pre.endsWith(dbl) && post.startsWith(dbl)) {
      return {
        value: pre.slice(0, pre.length - dbl.length) + sel + post.slice(dbl.length),
        start: start - dbl.length,
        end: end - dbl.length,
      }
    }
    return {
      value: pre.slice(0, pre.length - before.length) + sel + post.slice(after.length),
      start: start - before.length,
      end: end - before.length,
    }
  }
  // Маркеры уже внутри самого выделения — снимаем их.
  if (sel.startsWith(before) && sel.endsWith(after) && sel.length > before.length + after.length) {
    return {
      value: pre + sel.slice(before.length, sel.length - after.length) + post,
      start,
      end: end - before.length - after.length,
    }
  }
  // Обычная обёртка выделения.
  if (sel) {
    return {
      value: pre + before + sel + after + post,
      start: start + before.length,
      end: end + before.length,
    }
  }
  // Пустое выделение — вставляем с плейсхолдером и выделяем его.
  return {
    value: pre + before + placeholder + after + post,
    start: start + before.length,
    end: start + before.length + placeholder.length,
  }
}

/**
 * Префикс строки (заголовок «# ») с тогглом: повторный клик снимает
 * любой существующий уровень «# …». Работает по строке, содержащей
 * начало выделения.
 */
export function mdLinePrefix(
  value: string,
  start: number,
  end: number,
  prefix: string,
): MdResult {
  const lineStart = value.lastIndexOf('\n', start - 1) + 1
  const pre = value.slice(0, lineStart)
  const rest = value.slice(lineStart)
  const existing = rest.match(/^#{1,6}[ \t]+/)
  if (existing) {
    const cut = existing[0].length
    return {
      value: pre + rest.slice(cut),
      start: Math.max(lineStart, start - cut),
      end: Math.max(lineStart, end - cut),
    }
  }
  return {
    value: pre + prefix + rest,
    start: start + prefix.length,
    end: end + prefix.length,
  }
}
