
// Commented out chatbot/Gemini integration for now
// import { GoogleGenAI, Type } from "@google/genai";

// const ai = new GoogleGenAI({ apiKey: process.env.API_KEY });

export const analyzeDesignImage = async (base64Image: string) => {
  // Temporarily disabled - returns mock data
  return {
    roomType: "Living Room",
    style: "Modern",
    complexity: 5,
    estimatedPrice: "₹5,00,000 - ₹10,00,000",
    keyFeatures: ["Modern design", "Clean lines"]
  };
  
  /* Original implementation - commented out
  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: {
      parts: [
        { inlineData: { data: base64Image, mimeType: "image/jpeg" } },
        { text: "Analyze this interior design image. Identify the room type, primary style, complexity level (1-10), and estimated price range in INR." }
      ]
    },
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.OBJECT,
        properties: {
          roomType: { type: Type.STRING },
          style: { type: Type.STRING },
          complexity: { type: Type.NUMBER },
          estimatedPrice: { type: Type.STRING },
          keyFeatures: { type: Type.ARRAY, items: { type: Type.STRING } }
        }
      }
    }
  });
  
  return JSON.parse(response.text);
  */
};

export const generateSmartQuotes = async (requirement: any) => {
  // Temporarily disabled - returns mock data
  return [];
  
  /* Original implementation - commented out
  const response = await ai.models.generateContent({
    model: "gemini-3-flash-preview",
    contents: `Based on this user requirement: ${JSON.stringify(requirement)}, generate 3 unique and realistic response quotes from professional interior designers. Include estimated cost, timeline, and a professional message.`,
    config: {
      responseMimeType: "application/json",
      responseSchema: {
        type: Type.ARRAY,
        items: {
          type: Type.OBJECT,
          properties: {
            designerName: { type: Type.STRING },
            estimatedCost: { type: Type.NUMBER },
            timeline: { type: Type.STRING },
            message: { type: Type.STRING },
            styleMatchScore: { type: Type.NUMBER }
          }
        }
      }
    }
  });

  return JSON.parse(response.text);
  */
};
