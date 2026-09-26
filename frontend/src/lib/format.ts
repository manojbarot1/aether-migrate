export const gib = (mib: number | null | undefined) => (mib == null ? "—" : `${+(mib / 1024).toFixed(1)} GiB`);
