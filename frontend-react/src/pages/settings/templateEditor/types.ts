/** The printed sheet, in screen pixels at 96 dpi: width, page height (null for a receipt roll,
 *  which has no fixed height), minimum sheet height on screen, margin, font. */
export type PageBox = { w: number; h: number | null; minH: number; pad: number; font: string };
