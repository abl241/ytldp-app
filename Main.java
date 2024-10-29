package com.example.ytdlp;

import javafx.application.Application;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TextField;
import javafx.scene.layout.VBox;
import javafx.stage.Stage;
import org.json.JSONObject;
import com.example.ytdlp.Functions;

import java.io.*;

public class Main extends Application {

    @Override
    public void start(Stage primaryStage) {
        String dir = "";
        Button downloadVideosButton = new Button("Download videos");
        downloadVideosButton.setOnAction(e -> downloadVideosFunction());

        Button downloadThumbsButton = new Button("Download thumbnails only");
        downloadThumbsButton.setOnAction(e -> downloadThumbsFunction());

        Button mergeThumbsButton = new Button("Embed new thumbnail to video");
        mergeThumbsButton.setOnAction(e -> embedThumbsFunction());

        // Layout to hold the buttons
        VBox vbox = new VBox(10); // VBox with 10px spacing
        vbox.getChildren().addAll(downloadVideosButton, downloadThumbsButton, mergeThumbsButton);

        // Create and set the scene
        Scene scene = new Scene(vbox, 800, 800);
        primaryStage.setTitle("YTDLP");
        primaryStage.setScene(scene);
        primaryStage.show();
    }

    // Function for Button 1
    private void downloadVideosFunction() {
        Stage newWindow = new Stage();

        Label text = new Label("Enter YouTube URL");

        TextField textField = new TextField();
        textField.setPromptText("Youtube URL");

        Button downloadButton = new Button("Download");
        downloadButton.setOnAction(e -> ytdlpFunction(textField));

        Label label = new Label("Current directory:");
        TextField curDir = new TextField();
        curDir.setPromptText(retrieveData("dir"));
        Button changeDirButton = new Button("Change directory");
        changeDirButton.setOnAction(e -> writeData("dir", curDir.getText()));

        VBox vbox = new VBox(10); // 10px spacing
        vbox.getChildren().addAll(text, textField, downloadButton, label, curDir, changeDirButton);

        // Set the scene for the new window
        Scene newScene = new Scene(vbox, 750, 500);

        // Set window title and scene, then show the window
        newWindow.setTitle("Download");
        newWindow.setScene(newScene);

        // Show the new window
        newWindow.show();
    }


    private void enterButton(Stage newWindow, TextField textField, Label curDir) {
        writeData("dir", textField.getText());
        //System.out.println("Directory: " + retrieveData("dir"));
        curDir.setText("current dir: " + retrieveData("dir"));
        newWindow.close();
    }

    private static void ytdlpFunction(TextField textField) {
        Functions.ytdlpFunction(textField);
    }

    private void downloadThumbsFunction() {
        Stage newWindow = new Stage();

        Label text = new Label("Enter YouTube URL");

        TextField textField = new TextField();
        textField.setPromptText(retrieveData("thumburl"));

        Button downloadThumbsButton = new Button("Download");
        downloadThumbsButton.setOnAction(e -> ytdlpThumbsFunction(textField));

        Label label = new Label("Current directory:");

        TextField textfield2 = new TextField();
        textfield2.setPromptText(retrieveData("thumbdir"));

        Button changeThumbDirButton = new Button("Change thumbnail directory");
        changeThumbDirButton.setOnAction(e -> writeData("thumbdir", textfield2.getText()));

        VBox vbox = new VBox(10); // 10px spacing
        vbox.getChildren().addAll(text, textField, downloadThumbsButton, label, textfield2, changeThumbDirButton);

        // Set the scene for the new window
        Scene newScene = new Scene(vbox, 750, 500);

        // Set window title and scene, then show the window
        newWindow.setTitle("Download");
        newWindow.setScene(newScene);

        // Show the new window
        newWindow.show();
    }

    private void ytdlpThumbsFunction(TextField textField) {
        Functions.ytdlpThumbsFunction(textField);
    }

    private static void embedThumbsFunction() {
        Stage newWindow = new Stage();

        Label text = new Label("Video directory");
        TextField vidDir = new TextField();
        vidDir.setPromptText(retrieveData("embedvideodir"));

        Label text2 = new Label("Thumbnail directory");
        TextField thumbDir = new TextField();
        thumbDir.setPromptText(retrieveData("embedthumbdir"));

        Button embed = new Button("Embed");
        embed.setOnAction(e -> {writeData("embedvideodir", vidDir.getText()); writeData("embedthumbdir", thumbDir.getText()); Functions.embed();});

        Label label = new Label("Current output directory:");

        TextField changeDir = new TextField();
        changeDir.setPromptText(retrieveData("embedout"));

        Button changeDirButton = new Button("Change output directory");
        changeDirButton.setOnAction(e -> writeData("embedout", changeDir.getText()));

        Label note = new Label("*Output directory must be different than video or thumbnail directory");
        Label note2 = new Label("*Thumbnail files must be .jpeg, .jpg, or .png originally");

        VBox vbox = new VBox(10);
        vbox.getChildren().addAll(text, vidDir, text2, thumbDir, embed, label, changeDir, changeDirButton, note, note2);

        Scene newScene = new Scene(vbox, 750, 500);
        newWindow.setTitle("Embed Thumbnails");
        newWindow.setScene(newScene);
        newWindow.show();
    }


    public static String retrieveData(String key) {
        return Functions.retrieveData(key);
    }
    public static void writeData(String key, String value) {
        Functions.writeData(key, value);
    }

    public static void main(String[] args) {
        launch(args);
    }
}
