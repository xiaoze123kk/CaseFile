export type CompileTarget =
  "novel" | "script" | "interactive" | "dossier" | "test";

/** Paper objects, drawn at illustration size for the work-entry selector. */
export function CompileTargetIcon({ target }: { target: CompileTarget }) {
  return (
    <svg viewBox="0 0 160 160" fill="none" aria-hidden="true" focusable="false">
      <ellipse
        cx="80"
        cy="139"
        rx="49"
        ry="6"
        fill="currentColor"
        opacity=".07"
      />
      <g
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {target === "novel" ? (
          <>
            <path
              d="M35 33 112 25q10-1 10 10v94l-78 9q-12 1-12-10V43q0-8 3-10Z"
              fill="#f7eee3"
            />
            <path d="m44 126 78-8v11l-78 9q-12 0-12-8t12-9" fill="#fffdf7" />
            <path
              d="m44 34 68-7q6 0 6 6v84l-74 9Z"
              fill="currentColor"
              opacity=".16"
              stroke="none"
            />
            <path d="M45 33v86m4 12 66-7" opacity=".6" />
            <path d="m65 62 35-4m-35 14 25-3" strokeWidth="3" />
            <path
              d="m90 28 13-1v28l-7-4-6 6Z"
              fill="currentColor"
              stroke="none"
            />
            <path d="m66 94 31-3" opacity=".4" />
          </>
        ) : target === "script" ? (
          <>
            <path d="m42 30 67 7 9 94-78 4-8-96Z" fill="#efe9da" />
            <path d="m49 22 67 12-5 94-75-11Z" fill="#fffcf3" />
            <path d="m57 43 36 6m-38 8 43 7m-45 8 20 3" opacity=".55" />
            <path
              d="M83 79q19 6 37-3v30q-2 20-18 27-18-10-19-29Z"
              fill="#e8dcc4"
            />
            <path d="m91 93 6 2m11 0 6-3m-19 20q8 7 15-1" strokeWidth="2.5" />
            <path d="m42 100 25 4m-25 5 17 3" opacity=".4" />
          </>
        ) : target === "interactive" ? (
          <>
            <path d="M79 58v24m-39 18V83h80v17M79 83v17" strokeWidth="3" />
            <rect x="57" y="23" width="46" height="37" rx="9" fill="#e5e9e3" />
            <path d="m75 33 13 8-13 8Z" fill="currentColor" stroke="none" />
            <rect x="22" y="102" width="36" height="30" rx="7" fill="#f9fcf5" />
            <rect x="63" y="102" width="34" height="30" rx="7" fill="#e5e9e3" />
            <rect
              x="103"
              y="102"
              width="36"
              height="30"
              rx="7"
              fill="#f9fcf5"
            />
            <path d="m34 117 4 4 8-9m26 5h15m28 0h12" opacity=".7" />
          </>
        ) : target === "dossier" ? (
          <>
            <path
              d="M24 53V40q0-7 7-7h34l12 13h51q8 0 8 8v71H24Z"
              fill="#e6dcca"
            />
            <path d="m43 47 64-9 12 75-66 8Z" fill="#fffdf6" />
            <path d="m56 57 31-4m-29 13 42-6" opacity=".5" />
            <path
              d="M21 72q-1-7 6-7h40l11 9h56q7 0 6 7l-8 48H29Z"
              fill="#f0e5d0"
            />
            <path d="M58 91h47v22H58Z" fill="#fffaf0" strokeOpacity=".5" />
            <path d="M69 102h25" opacity=".5" />
          </>
        ) : (
          <>
            <rect x="38" y="32" width="84" height="101" rx="7" fill="#e4e4dc" />
            <rect
              x="45"
              y="39"
              width="70"
              height="86"
              rx="3"
              fill="#fffdf6"
              strokeOpacity=".4"
            />
            <path d="M65 30q0-12 15-12t15 12h8v14H57V30Z" fill="#d9dcca" />
            <path d="m56 65 4 4 8-10m-12 28 4 4 8-10m-12 28 4 4 8-10M79 65h24M79 87h24m-24 22h17" />
            <circle cx="80" cy="28" r="3" fill="#fffdf6" />
          </>
        )}
      </g>
    </svg>
  );
}
