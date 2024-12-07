package com.example.ytdlp;


import javafx.scene.control.TextField;
import org.json.JSONObject;

import java.io.*;
import java.nio.file.Paths;
import java.util.Arrays;
import java.util.List;

public class Functions {
    public static void ytdlpFunction(TextField textField) {
        writeData("url", textField.getText());

        String dir = retrieveData("dir");
        String url = retrieveData("url");
        System.out.println("Directory: " + dir);
        System.out.println("URL: " + url);

        // Variables to pass to the Python script
        try {
            // Specify the Python script path
            String pythonScriptPath = Paths.get( "DownloadMusicAndThumbnails.py").toString();
            // Create a process builder and add arguments
            ProcessBuilder pb = new ProcessBuilder("python3", pythonScriptPath, url, dir);
            Process process = pb.start();

            // Capture the output from the script
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                System.out.println(line);
            }

            process.waitFor();
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
        }
    }

    public static void ytdlpThumbsFunction(TextField textField) {
        writeData("thumburl", textField.getText());

        String dir = retrieveData("thumbdir");
        String url = retrieveData("thumburl");
        System.out.println("Thumbnail Directory: " + dir);
        System.out.println("Thumbnail URL: " + url);

        // Variables to pass to the Python script
        try {
            // Specify the Python script path
            String pythonScriptPath = Paths.get( "DownloadThumbnails.py").toString();
            // Create a process builder and add arguments
            ProcessBuilder pb = new ProcessBuilder("python3", pythonScriptPath, url, dir);
            Process process = pb.start();

            // Capture the output from the script
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                System.out.println(line);
            }

            process.waitFor();
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
        }
    }

    public static void embed() {
        String vidDir = retrieveData("embedvideodir");
        String thumbDir = retrieveData("embedthumbdir");
        if(new File(thumbDir).isDirectory()) {
            embedFromFolder();
        } else {
            String output = retrieveData("embedout") + vidDir.substring(vidDir.lastIndexOf("/"));
            // FFmpeg command
            String[] command = {
                    "ffmpeg",
                    "-i", vidDir,
                    "-i", thumbDir,
                    "-acodec", "libmp3lame",
                    "-b:a", "256k",
                    "-c:v", "copy",
                    "-map", "0:a:0",
                    "-map", "1:v:0",
                    output
            };
            // Run the command using ProcessBuilder
            ProcessBuilder processBuilder = new ProcessBuilder(command);
            processBuilder.redirectErrorStream(true); // Combine error and output streams
            try {
                Process process = processBuilder.start();
                process.waitFor(); // Wait for the process to finish
                System.out.println("Process finished successfully.");
            } catch (IOException | InterruptedException e) {
                e.printStackTrace();
            }
        }
    }

    public static void embedFromFolder() {
        List<String> acceptedFileTypes = Arrays.asList(".jpg", ".jpeg", ".png");
        String thumbDir = retrieveData("embedthumbdir");
        String videoDir = retrieveData("embedvideodir");
        File path = new File(thumbDir);

        File[] files = path.listFiles();
        for (int i = 0; i < files.length; i++) {
            if (files[i].getName().lastIndexOf('.') != -1 && acceptedFileTypes.contains(files[i].getName().substring(files[i].getName().lastIndexOf('.')))) { //this line weeds out other directories/folders
                String thumb = files[i].getName();
                String video = videoDir + "/" + thumb.substring(0, thumb.lastIndexOf(".")) + ".mp3";
                thumb = thumbDir + "/" + thumb;
                System.out.println("Thumb: " + thumb);
                System.out.println("Video: " + video);
                embedFromFolderHelper(video, thumb);
            }
        }
    }
    public static void embedFromFolderHelper(String vidDir, String thumbDir) {
        String output = retrieveData("embedout") + vidDir.substring(vidDir.lastIndexOf("/"));
        // FFmpeg command
        String[] command = {
                "ffmpeg",
                "-i", vidDir,
                "-i", thumbDir,
                "-acodec", "libmp3lame",
                "-b:a", "256k",
                "-c:v", "copy",
                "-map", "0:a:0",
                "-map", "1:v:0",
                output
        };
        // Run the command using ProcessBuilder
        ProcessBuilder processBuilder = new ProcessBuilder(command);
        processBuilder.redirectErrorStream(true); // Combine error and output streams
        try {
            Process process = processBuilder.start();
            process.waitFor(); // Wait for the process to finish
            System.out.println("Process finished successfully.");
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
        }
    }

    public static void ytdlpVideosFunction(TextField textField) {
        writeData("url", textField.getText());

        String dir = retrieveData("viddir");
        String url = retrieveData("url");
        System.out.println("Directory: " + dir);
        System.out.println("URL: " + url);

        // Variables to pass to the Python script
        try {
            // Specify the Python script path
            String pythonScriptPath = Paths.get( "DownloadVideos.py").toString();
            // Create a process builder and add arguments
            ProcessBuilder pb = new ProcessBuilder("python3", pythonScriptPath, url, dir);
            Process process = pb.start();

            // Capture the output from the script
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                System.out.println(line);
            }

            process.waitFor();
        } catch (IOException | InterruptedException e) {
            e.printStackTrace();
        }
    }

    public static String retrieveData(String key) {
        StringBuilder jsonData = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new FileReader("data.json"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                jsonData.append(line);
            }
            JSONObject jsonObject = new JSONObject(jsonData.toString());

            // Get the value for the specified key
            if (jsonObject.has(key)) {
                return jsonObject.get(key).toString(); // Return the value as a string
            } else {
                return "Key not found: " + key; // Key does not exist
            }
        } catch (IOException e) {
            e.printStackTrace();
            return "Error reading JSON file.";
        }
    }
    public static void writeData(String key, String value) {
        StringBuilder jsonData = new StringBuilder();
        try (BufferedReader reader = new BufferedReader(new FileReader("data.json"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                jsonData.append(line);
            }
            JSONObject jsonObject = new JSONObject(jsonData.toString());

            // Add or update the key-value pair
            jsonObject.put(key, value);

            // Write the updated JSON object back to the file
            try (FileWriter file = new FileWriter("data.json")) {
                file.write(jsonObject.toString(4)); // Write the updated JSON with pretty print
                //System.out.println("Updated key-value pair in data.json");
            }
        } catch (IOException e) {
            e.printStackTrace();
        }
    }
}

