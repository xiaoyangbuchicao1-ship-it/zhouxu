import Image from "next/image";
import styles from "./team-section.module.css";

// Populate only after identity, sources and publishing permission are verified.
type TeamMember = { name: string; role: string; portrait?: string; portraitAlt?: string; biography: string; source: string; sourceLabel: string };
const verifiedMembers: TeamMember[] = [];

export function TeamSection() {
  if (!verifiedMembers.length) return null;
  return <section className={styles.section} aria-labelledby="team-heading">
    <h2 id="team-heading">团队与专业背景</h2>
    {verifiedMembers.map(person => <article key={person.name}>
      {person.portrait ? <Image src={person.portrait} alt={person.portraitAlt || person.name} width={480} height={600} sizes="(max-width: 767px) 100vw, 30vw" /> : null}
      <div><h3>{person.name}</h3><p className={styles.role}>{person.role}</p><p>{person.biography}</p><a href={person.source} target="_blank" rel="noreferrer">{person.sourceLabel}</a></div>
    </article>)}
  </section>;
}
