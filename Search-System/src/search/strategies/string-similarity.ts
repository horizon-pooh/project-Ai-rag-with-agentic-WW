export function levenshteinDistance(
  source: string,
  target: string
): number {
  const rows = source.length + 1;
  const columns = target.length + 1;

  const matrix = Array.from(
    { length: rows },
    () => Array<number>(columns).fill(0)
  );

  for (let row = 0; row < rows; row++) {
    matrix[row][0] = row;
  }

  for (
    let column = 0;
    column < columns;
    column++
  ) {
    matrix[0][column] = column;
  }

  for (let row = 1; row < rows; row++) {
    for (
      let column = 1;
      column < columns;
      column++
    ) {
      const substitutionCost =
        source[row - 1] ===
        target[column - 1]
          ? 0
          : 1;

      matrix[row][column] = Math.min(
        matrix[row - 1][column] + 1,
        matrix[row][column - 1] + 1,
        matrix[row - 1][column - 1] +
          substitutionCost
      );
    }
  }

  return matrix[source.length][target.length];
}

export function stringSimilarity(
  source: string,
  target: string
): number {
  if (source === target) {
    return 1;
  }

  if (!source || !target) {
    return 0;
  }

  const distance =
    levenshteinDistance(source, target);

  const longestLength = Math.max(
    source.length,
    target.length
  );

  return 1 - distance / longestLength;
}