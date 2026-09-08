export type TextDelta = { kind: "same" | "delete" | "insert"; text: string };
/** Unicode-safe LCS with a bounded cost; fallback still reconstructs both exact texts. */
export function novelTextDiff(before: string, after: string): TextDelta[] {
  const a = Array.from(before),
    b = Array.from(after);
  let prefix = 0,
    suffix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix])
    prefix++;
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  )
    suffix++;
  const left = a.slice(prefix, a.length - suffix),
    right = b.slice(prefix, b.length - suffix),
    result: TextDelta[] = [];
  function add(kind: TextDelta["kind"], text: string) {
    if (!text) return;
    const last = result.at(-1);
    if (last?.kind === kind) last.text += text;
    else result.push({ kind, text });
  }
  add("same", a.slice(0, prefix).join(""));
  if (left.length * right.length > 1_000_000) {
    add("delete", left.join(""));
    add("insert", right.join(""));
  } else {
    const width = right.length + 1,
      dp = new Uint32Array((left.length + 1) * width);
    for (let i = left.length - 1; i >= 0; i--)
      for (let j = right.length - 1; j >= 0; j--)
        dp[i * width + j] =
          left[i] === right[j]
            ? 1 + dp[(i + 1) * width + j + 1]
            : Math.max(dp[(i + 1) * width + j], dp[i * width + j + 1]);
    let i = 0,
      j = 0;
    while (i < left.length || j < right.length) {
      if (i < left.length && j < right.length && left[i] === right[j]) {
        add("same", left[i++]);
        j++;
      } else if (
        i < left.length &&
        (j === right.length || dp[(i + 1) * width + j] >= dp[i * width + j + 1])
      )
        add("delete", left[i++]);
      else add("insert", right[j++]);
    }
  }
  if (suffix) add("same", a.slice(a.length - suffix).join(""));
  return result;
}
