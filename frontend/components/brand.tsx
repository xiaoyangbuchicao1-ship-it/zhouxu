import Image from "next/image";
import Link from "next/link";
import styles from "./brand.module.css";

type BrandProps = {
  href?: string;
  inverse?: boolean;
  compact?: boolean;
  markTone?: "white" | "silver";
};

export function Brand({ href = "/", inverse = false, compact = false, markTone = "white" }: BrandProps) {
  return (
    <Link
      href={href}
      className={`${styles.brand} ${inverse ? styles.inverse : ""} ${compact ? styles.compact : ""}`}
      aria-label="舟序首页"
    >
      <span className={`${styles.mark} ${markTone === "silver" ? styles.silver : ""}`}>
        <Image src="/zhou-xu-mark.png" width={56} height={56} sizes="44px" alt="" />
      </span>
      <span className={styles.words}>
        <strong>舟序</strong>
        <small>ZHOUXU</small>
      </span>
    </Link>
  );
}
