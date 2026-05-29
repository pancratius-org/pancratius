const RU_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"];
const RU_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"];
const RU_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"];
const RU_ONES_FEM = ["ноль", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"];

const EN_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];
const EN_TEENS = ["ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

function capitalize(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function wordAt(words: readonly string[], index: number, context: string): string {
  const word = words[index];
  if (word === undefined) throw new Error(`${context}: no word for index ${index}`);
  return word;
}

function spellRu(n: number, feminine: boolean): string {
  if (n < 0 || n > 99 || !Number.isInteger(n)) return String(n);
  const ones = feminine ? RU_ONES_FEM : RU_ONES;
  if (n < 10) return wordAt(ones, n, "Russian ones");
  if (n < 20) return wordAt(RU_TEENS, n - 10, "Russian teens");
  const t = Math.floor(n / 10);
  const o = n % 10;
  const tens = wordAt(RU_TENS, t, "Russian tens");
  return o === 0 ? tens : `${tens} ${wordAt(ones, o, "Russian ones")}`;
}

function spellEn(n: number): string {
  if (n < 0 || n > 99 || !Number.isInteger(n)) return String(n);
  if (n < 10) return wordAt(EN_ONES, n, "English ones");
  if (n < 20) return wordAt(EN_TEENS, n - 10, "English teens");
  const t = Math.floor(n / 10);
  const o = n % 10;
  const tens = wordAt(EN_TENS, t, "English tens");
  return o === 0 ? tens : `${tens}-${wordAt(EN_ONES, o, "English ones")}`;
}

export function spellEnglishCardinal(n: number): string {
  return capitalize(spellEn(n));
}

export function spellRussianCardinal(n: number, options: { feminine?: boolean } = {}): string {
  return capitalize(spellRu(n, options.feminine ?? false));
}
