import { initializeApp } from "https://www.gstatic.com/firebasejs/9.22.0/firebase-app.js";
import { getFirestore, collection, getDocs, doc, setDoc, getDoc, addDoc, updateDoc, deleteDoc, collectionGroup, increment, query, orderBy, onSnapshot, where, limit } from "https://www.gstatic.com/firebasejs/9.22.0/firebase-firestore.js";
import { getAuth, signInWithCustomToken, signOut, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/9.22.0/firebase-auth.js";
import { FC } from "./config.js";

const fb = initializeApp(FC);
export const db = getFirestore(fb);
export const auth = getAuth(fb);

export { collection, getDocs, doc, setDoc, getDoc, addDoc, updateDoc, deleteDoc, collectionGroup, increment, query, orderBy, onSnapshot, where, limit,
         signInWithCustomToken, signOut, onAuthStateChanged };
